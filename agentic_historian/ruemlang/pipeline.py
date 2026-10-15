"""pipeline.py — the Rümlang test, end to end, as a resumable CLI on tei.

    python -m agentic_historian.ruemlang.pipeline plan      --collection Rümlang
    python -m agentic_historian.ruemlang.pipeline recognize [--limit 3]
    python -m agentic_historian.ruemlang.pipeline adjudicate --reference druck1528.txt
    python -m agentic_historian.ruemlang.pipeline xml
    python -m agentic_historian.ruemlang.pipeline upload    [--dry-run]
    python -m agentic_historian.ruemlang.pipeline run       --collection Rümlang --reference … --limit 3

Every step reads what the step before wrote under ``--out`` (default
``VLM_TEST_ROOT/ruemlang``), one directory per page::

    <out>/plan.json
    <out>/pages/doc<docId>_p<pageNr>/page.json        the Transkribus page record
                                     image.<ext>      the page image as Transkribus serves it
                                     rec_<model>.json the gateway's answer, verbatim
                                     adjudicated.json cases, passage, decisions
                                     page.xml         what goes back
                                     uploaded.json    Transkribus' answer — the page is done

A page with a file for a step is skipped by that step, so an interrupted run is
resumed by running the same command again, and ``--force`` is the only way to
redo work. Secrets come from the environment (``TRANSKRIBUS_USER/PASS``,
``ATR_API_KEY``, ``GPUSTACK_API_KEY``) exactly as the rest of the project reads
them; nothing here takes a password on the command line.

Recognition iterates **model-major** for the same reason the batch runner does:
the gateway's vLLM models are lazy on one card, and alternating two of them per
page would reload 12 GB per page.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

PKG = Path(__file__).resolve().parent.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from loguru import logger                     # noqa: E402

import config                                 # noqa: E402
import transkribus as tk                      # noqa: E402
from ruemlang import adjudicate, pagexml, textalign      # noqa: E402
from ruemlang import transkribus_pages as tp             # noqa: E402

#: StAB A V 1446, the protocol in Eberhard von Rümlang's hand, as thodel/reformation
#: has it (config/disputation_transkribus.json): collection 2313234, document 13922129.
DEFAULT_COLLECTION = "2313234"
DEFAULT_DOC = 13922129
DOCS_DIR = PKG.parent / "docs" / "ruemlang"
DEFAULT_REFERENCE = DOCS_DIR / "druck1528_transkription.txt"
DEFAULT_ANCHORS = DOCS_DIR / "print_anchors.json"
DEFAULT_LINE_MODEL = "qwen3vl-medieval-german-v3"
DEFAULT_TROCR_MODEL = "trocr-kurrent-xvi-xvii"
DEFAULT_PAGE_MODEL = "qwen3.5-4b-german-kurrent-xvi-xix-page-v1"
MAX_SIDE = 3500          # px; larger page images are downscaled for the gateway, coordinates scaled back


# ── layout on disk ───────────────────────────────────────────────────────────

def out_root(args) -> Path:
    root = Path(args.out) if args.out else Path(config.VLM_TEST_ROOT) / "ruemlang"
    root.mkdir(parents=True, exist_ok=True)
    return root


def page_dir(root: Path, p: tp.PageInfo) -> Path:
    d = root / "pages" / p.key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _login() -> tp.TkSession:
    ts = tp.TkSession().authenticate()
    logger.info(f"[ruemlang] Transkribus: authenticated via {ts.source}")
    return ts


def _planned(root: Path, args) -> tuple[dict, list[tp.PageInfo]]:
    plan = root / "plan.json"
    if not plan.exists():
        raise SystemExit(f"no plan at {plan} — run `plan --collection …` first")
    collection, pages = tp.read_plan(plan)
    if getattr(args, "limit", None):
        pages = pages[:args.limit]
    return collection, pages


# ── plan ─────────────────────────────────────────────────────────────────────

def cmd_plan(args) -> int:
    root = out_root(args)
    ts = _login()
    col = tp.find_collection(ts, args.collection)
    logger.info(f"[ruemlang] collection {col.get('colId')} {col.get('colName')!r}")
    docs = [int(d) for d in args.doc] if args.doc else None
    pages = tp.collection_pages(ts, int(col["colId"]), docs=docs)
    unseg = [p for p in pages if tp.wanted(p, include_untranscribed=not args.only_unsegmented)]
    by_status: dict[str, int] = {}
    for p in pages:
        by_status[str(p.status)] = by_status.get(str(p.status), 0) + 1
    print(f"collection {col['colId']} {col.get('colName')!r}, document(s) {docs or 'all'}: "
          f"{len(pages)} page(s); {sum(tp.is_unsegmented(p) for p in pages)} without lines, "
          f"{sum(tp.is_untranscribed(p) for p in pages)} with lines but no text; "
          f"{len(unseg)} selected; statuses: {by_status}")
    for p in unseg[:args.show]:
        print(f"  {p.key}  {p.doc_title[:50]!r}  {p.width}x{p.height}  status={p.status}")
    if len(unseg) > args.show:
        print(f"  … {len(unseg) - args.show} more")
    if args.dry_run:
        print("dry run: plan not written")
        return 0
    tp.write_plan(root / "plan.json", col, unseg)
    for p in unseg:
        _save(page_dir(root, p) / "page.json", p.to_dict())
    print(f"plan written: {root / 'plan.json'} ({len(unseg)} pages)")
    return 0


# ── recognize ────────────────────────────────────────────────────────────────

def _prepare_image(src: Path, dest_dir: Path) -> tuple[Path, float]:
    """The image to send and the factor that maps its pixels back to the original."""
    from PIL import Image
    with Image.open(src) as im:
        w, h = im.size
        if max(w, h) <= MAX_SIDE:
            return src, 1.0
        f = MAX_SIDE / max(w, h)
        small = dest_dir / "image_gateway.jpg"
        if not small.exists():
            im.convert("RGB").resize((round(w * f), round(h * f))).save(small, quality=92)
        return small, 1.0 / f


def cmd_recognize(args) -> int:
    root = out_root(args)
    collection, pages = _planned(root, args)
    ts = _login()
    from agent_a.kraken_client import KrakenHTTPClient
    models = [args.line_model, args.trocr_model, args.page_model]
    models = [m for m in models if m]
    # Images first, all of them, so a gateway hiccup does not interleave with Transkribus.
    for p in pages:
        d = page_dir(root, p)
        imgs = list(d.glob("image.*"))
        if imgs and not any(i.name.endswith(".part") for i in imgs):
            continue
        ext = Path(p.image_url.split("?")[0]).suffix.lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
            ext = ".jpg"
        tp.download_image(ts, p, d / f"image{ext}")
        logger.info(f"[ruemlang] {p.key}: image fetched")
    failures = 0
    with KrakenHTTPClient(timeout=float(args.timeout)) as gw:
        for model in models:                                   # model-major
            for p in pages:
                d = page_dir(root, p)
                rec = d / f"rec_{model}.json"
                if rec.exists() and not args.force:
                    continue
                img = next(i for i in d.glob("image.*") if not i.name.endswith(".part"))
                send, scale = _prepare_image(img, d)
                t0 = time.monotonic()
                try:
                    res = gw.recognize(send, model=model)
                except Exception as exc:                       # noqa: BLE001 — record, continue
                    failures += 1
                    logger.error(f"[ruemlang] {p.key} {model}: {type(exc).__name__}: {exc}")
                    _save(d / f"rec_{model}.error.json",
                          {"model": model, "error": f"{type(exc).__name__}: {exc}"})
                    if failures >= 5 and failures == sum(1 for _ in d.parent.glob("*/rec_*.error.json")):
                        pass
                    continue
                payload = {
                    "model": model, "engine": res.engine, "text": res.text,
                    "lines": list(res.lines or []), "confidence": res.confidence,
                    "segmented_by": res.segmented_by, "timing_ms": res.timing_ms,
                    "truncated": bool(res.truncated), "gateway_version": res.service_version,
                    "image_sent": send.name, "scale_to_original": scale,
                    "wall_s": round(time.monotonic() - t0, 1),
                }
                _save(rec, payload)
                logger.info(f"[ruemlang] {p.key} {model}: {len(res.lines or [])} line(s), "
                            f"{len(res.text or '')} chars, {payload['wall_s']}s")
    print(f"recognized {len(pages)} page(s) x {len(models)} model(s); failures: {failures}")
    return 1 if failures and failures == len(pages) * len(models) else 0


# ── adjudicate ───────────────────────────────────────────────────────────────

def _reference(args) -> Optional[textalign.Reference]:
    files = [Path(f) for f in (args.reference or [])] or ([DEFAULT_REFERENCE] if DEFAULT_REFERENCE.exists() else [])
    if not files:
        logger.warning("[ruemlang] no reference text: the print will not be consulted")
        return None
    text = "\n\n".join(f.read_text(encoding="utf-8") for f in files)
    logger.info(f"[ruemlang] reference: {', '.join(f.name for f in files)} ({len(text)} chars)")
    return textalign.Reference(text)


def _anchors(args) -> dict[int, tuple[int, int]]:
    """Manuscript page -> (first, last) print page, from the reformation
    project's ``print_anchors.json`` (alignment where it had one, interpolation
    between; both are only a prior here)."""
    path = Path(args.anchors) if getattr(args, "anchors", None) else DEFAULT_ANCHORS
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for a in data.get("pages", []):
        try:
            out[int(a["page"])] = (int(a["druck_von"]), int(a["druck_bis"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def cmd_adjudicate(args) -> int:
    root = out_root(args)
    collection, pages = _planned(root, args)
    ref = _reference(args)
    anchors = _anchors(args)
    llm = None if args.no_llm else adjudicate.default_llm
    done = 0
    for p in pages:
        d = page_dir(root, p)
        out = d / "adjudicated.json"
        if out.exists() and not args.force:
            done += 1
            continue
        a = _load(d / f"rec_{args.line_model}.json")
        if not a or not a.get("lines"):
            logger.warning(f"[ruemlang] {p.key}: no line-model result, skipped")
            continue
        b = _load(d / f"rec_{args.trocr_model}.json") or {}
        c = _load(d / f"rec_{args.page_model}.json") or {}
        a_lines = [(ln.get("text") or "") for ln in a["lines"]]
        b_lines = [(ln.get("text") or "") for ln in (b.get("lines") or [])]
        cases, meta = adjudicate.build_cases(a_lines, b_lines, c.get("text") or "", ref,
                                             page_hint=anchors.get(p.page_nr))
        decisions = adjudicate.decide_page(cases, llm, batch=args.batch)
        summary = {}
        for dec in decisions:
            k = dec.basis.split(":")[0]
            summary[k] = summary.get(k, 0) + 1
        _save(out, {"page": p.key, "models": {"A": args.line_model, "B": args.trocr_model,
                                              "C": args.page_model},
                    "arbiter": None if llm is None else getattr(config, "GPUSTACK_MODEL_TEXT", "?"),
                    "meta": meta, "decisions": [dec.to_dict() for dec in decisions],
                    "summary": summary})
        done += 1
        pas = meta["passage"]
        logger.info(f"[ruemlang] {p.key}: {len(decisions)} line(s) {summary}; print: "
                    + ("none" if not pas else
                       f"{'weak window' if pas['weak'] else 'located'} pages {pas['pages']} "
                       f"score {pas['score']}, {meta['lines_with_print']} line(s) with P"))
    print(f"adjudicated {done}/{len(pages)} page(s)")
    return 0


# ── xml ──────────────────────────────────────────────────────────────────────

def cmd_xml(args) -> int:
    root = out_root(args)
    collection, pages = _planned(root, args)
    written = 0
    for p in pages:
        d = page_dir(root, p)
        out = d / "page.xml"
        if out.exists() and not args.force:
            written += 1
            continue
        a = _load(d / f"rec_{args.line_model}.json")
        adj = _load(d / "adjudicated.json")
        if not a or not adj:
            logger.warning(f"[ruemlang] {p.key}: missing recognition or adjudication, skipped")
            continue
        decisions = {int(x["index"]): x for x in adj["decisions"]}
        lines = []
        for i, ln in enumerate(a["lines"]):
            dec = decisions.get(i)
            ln = dict(ln)
            if dec:
                ln["text"] = dec["text"]
                ln["custom"] = {"atr": {"basis": dec["basis"], "conf": f"{dec['confidence']:.2f}"}}
            lines.append(ln)
        width, height = p.width, p.height
        if not width or not height:
            from PIL import Image
            img = next(i for i in d.glob("image.*") if not i.name.endswith(".part"))
            with Image.open(img) as im:
                width, height = im.size
        img_name = Path(p.image_url.split("?")[0]).name or f"{p.key}.jpg"
        comment = pagexml.provenance_comment({
            "pipeline": "agentic_historian ruemlang", "models": adj["models"],
            "arbiter": adj.get("arbiter"), "print_passage_score":
                adj["meta"]["passage"]["score"] if adj["meta"].get("passage") else None,
            "summary": adj.get("summary")})
        xml, dropped = pagexml.build_pagexml(img_name, int(width), int(height), lines,
                                             scale=float(a.get("scale_to_original") or 1.0),
                                             comments=comment)
        out.write_text(xml, encoding="utf-8")
        if dropped:
            _save(d / "dropped_lines.json", dropped)
            logger.warning(f"[ruemlang] {p.key}: {len(dropped)} line(s) without geometry left out")
        written += 1
    print(f"PAGE XML written for {written}/{len(pages)} page(s)")
    return 0


# ── upload ───────────────────────────────────────────────────────────────────

def cmd_upload(args) -> int:
    root = out_root(args)
    collection, pages = _planned(root, args)
    ready = [p for p in pages if (page_dir(root, p) / "page.xml").exists()]
    todo = [p for p in ready if not (page_dir(root, p) / "uploaded.json").exists() or args.force]
    print(f"{len(ready)} page(s) have a PAGE XML, {len(todo)} to upload as {args.status} "
          f"into collection {collection['colId']} {collection.get('colName')!r}")
    if args.dry_run:
        for p in todo[:20]:
            print(f"  would POST doc {p.docid} page {p.page_nr} (parent ts {p.ts_id})")
        return 0
    ts = _login()
    ok = 0
    for p in todo:
        d = page_dir(root, p)
        xml = (d / "page.xml").read_text(encoding="utf-8")
        note = (f"agentic_historian ruemlang: {args.line_model} + {args.trocr_model} + "
                f"{args.page_model}, collated with the 1528 print, arbitrated by "
                f"{getattr(config, 'GPUSTACK_MODEL_TEXT', '?')}")
        try:
            answer = tp.post_pagexml(ts, p.colid, p.docid, p.page_nr, xml, status=args.status,
                                     note=note, parent_ts=p.ts_id)
        except tk.TranskribusError as exc:
            logger.error(f"[ruemlang] {p.key}: {exc}")
            continue
        _save(d / "uploaded.json", {"page": p.key, "status": args.status, "answer": answer,
                                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        ok += 1
        logger.info(f"[ruemlang] {p.key}: uploaded")
    print(f"uploaded {ok}/{len(todo)} page(s)")
    return 0 if ok == len(todo) else 1


def cmd_run(args) -> int:
    """All steps. A dry run still plans and reads — those are reversible, the
    upload is the one step that changes Transkribus and is the one left out."""
    dry = bool(getattr(args, "dry_run", False))
    args.dry_run = False
    for step in (cmd_plan, cmd_recognize, cmd_adjudicate, cmd_xml):
        rc = step(args)
        if rc:
            return rc
    args.dry_run = dry
    return cmd_upload(args)


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ruemlang", description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="run directory (default VLM_TEST_ROOT/ruemlang)")
    ap.add_argument("--limit", type=int, help="only the first N planned pages")
    ap.add_argument("--force", action="store_true", help="redo steps whose output exists")
    ap.add_argument("--line-model", default=DEFAULT_LINE_MODEL)
    ap.add_argument("--trocr-model", default=DEFAULT_TROCR_MODEL)
    ap.add_argument("--page-model", default=DEFAULT_PAGE_MODEL)
    sub = ap.add_subparsers(dest="command", required=True)

    def _plan_args(p):
        p.add_argument("--collection", default=DEFAULT_COLLECTION,
                       help=f"collection name (substring) or id (default {DEFAULT_COLLECTION})")
        p.add_argument("--doc", action="append", default=None,
                       help=f"restrict to these document ids (default {DEFAULT_DOC}, A V 1446 Rümlang; "
                            "pass --doc all for every document)")
        p.add_argument("--only-unsegmented", action="store_true",
                       help="pages without lines only; default also takes pages whose lines have no text")
        p.add_argument("--show", type=int, default=15)
        p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("plan", help="which pages have no segmentation")
    _plan_args(p); p.set_defaults(func=cmd_plan)

    p = sub.add_parser("recognize", help="read every planned page with the three models")
    p.add_argument("--timeout", type=float, default=600.0)
    p.set_defaults(func=cmd_recognize)

    def _adj_args(p):
        p.add_argument("--reference", action="append",
                       help=f"text file(s) of the 1528 print; repeatable (default {DEFAULT_REFERENCE.name})")
        p.add_argument("--anchors", help=f"print_anchors.json (default {DEFAULT_ANCHORS.name})")
        p.add_argument("--no-llm", action="store_true", help="deterministic only, no arbiter")
        p.add_argument("--batch", type=int, default=30, help="contested lines per arbiter call")

    p = sub.add_parser("adjudicate", help="collate the readings and the print, decide per line")
    _adj_args(p); p.set_defaults(func=cmd_adjudicate)

    p = sub.add_parser("xml", help="build a PAGE XML per page")
    p.set_defaults(func=cmd_xml)

    def _up_args(p, dry: bool = True):
        p.add_argument("--status", default="IN_PROGRESS", choices=list(tp.STATUSES))
        if dry:
            p.add_argument("--dry-run", action="store_true",
                           help="say what would be posted, post nothing")

    p = sub.add_parser("upload", help="post each PAGE XML to its Transkribus page")
    _up_args(p); p.set_defaults(func=cmd_upload)

    p = sub.add_parser("run", help="all steps in order; --dry-run plans and reports, "
                                   "reads and adjudicates, but writes no plan and posts nothing")
    _plan_args(p); p.add_argument("--timeout", type=float, default=600.0)
    _adj_args(p); _up_args(p, dry=False); p.set_defaults(func=cmd_run)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, "doc"):
        if args.doc is None:
            args.doc = [str(DEFAULT_DOC)]
        elif any(str(d).lower() == "all" for d in args.doc):
            args.doc = None
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

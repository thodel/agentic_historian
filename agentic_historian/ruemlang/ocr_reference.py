"""ocr_reference.py — a reference text of the 1528 print from OCR output.

The print (Froschauer, Zürich, 23 March 1528; VD16 H 504; e-rara 30973277, UB
Bern) has no usable full text online: e-rara's and Utrecht's OCR read the
Schwabacher as antiqua. kraken with CATMuS Gothic Print reads it well, one
PAGE XML (or ALTO) per page. This turns a directory of those into the single
text file ``pipeline.py adjudicate --reference`` takes, one block per page with
a marker naming the scan, so a passage the arbiter saw can be found again.

    python -m agentic_historian.ruemlang.ocr_reference ocr/ canvases.tsv > druck1528_ocr.txt
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


def _ns(root) -> str:
    return root.tag[1:root.tag.index("}")] if root.tag.startswith("{") else ""


def lines_of(xml_path: Path) -> list[str]:
    """Text lines of a PAGE XML or ALTO file, in document order."""
    root = ET.parse(xml_path).getroot()
    ns = _ns(root)
    out: list[str] = []
    if "alto" in ns:
        # kraken ALTO: one <String CONTENT=word> per word, <Glyph> children inside
        for tl in root.iter(f"{{{ns}}}TextLine"):
            words = [s.get("CONTENT", "") for s in tl.findall(f"{{{ns}}}String")]
            line = " ".join(w for w in words if w).strip()
            if line:
                out.append(line)
    else:
        for tl in root.iter(f"{{{ns}}}TextLine"):
            uni = tl.find(f"{{{ns}}}TextEquiv/{{{ns}}}Unicode")
            if uni is not None and (uni.text or "").strip():
                out.append(uni.text.strip())
    return out


def labels_from_tsv(path: Optional[Path]) -> dict[int, str]:
    """``canvas number -> label`` from the e-rara manifest listing (n, label, size, url)."""
    if not path:
        return {}
    out = {}
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[0].isdigit():
            out[int(parts[0])] = parts[1]
    return out


_NUM = re.compile(r"(\d+)")


def build(ocr_dir: Path, tsv: Optional[Path], *, source: str = "e-rara 30973277") -> str:
    labels = labels_from_tsv(tsv)
    files = sorted(Path(ocr_dir).glob("*.xml"),
                   key=lambda p: int(_NUM.search(p.stem).group(1)) if _NUM.search(p.stem) else 0)
    blocks = [f"# {source}: Handlung oder Acta gehaltner Disputation zuo Bernn in Uechtland, "
              f"Zürich: Froschauer, 23.3.1528 (VD16 H 504). OCR kraken/CATMuS Gothic Print; "
              f"{len(files)} page(s).\n"]
    for f in files:
        m = _NUM.search(f.stem)
        n = int(m.group(1)) if m else 0
        label = labels.get(n, "")
        lines = lines_of(f)
        blocks.append(f"[{source} canvas {n}{' ' + label if label else ''}]\n" + "\n".join(lines) + "\n")
    return "\n".join(blocks)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ocr_dir", type=Path)
    ap.add_argument("canvases_tsv", type=Path, nargs="?")
    ap.add_argument("--source", default="e-rara 30973277")
    args = ap.parse_args(argv)
    sys.stdout.write(build(args.ocr_dir, args.canvases_tsv, source=args.source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

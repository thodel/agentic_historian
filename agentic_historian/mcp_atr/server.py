"""
mcp_atr/server.py — a handful of operations on tei, reachable from a Claude session.

**Why this exists.** A Claude Code session in the cloud cannot open a socket to
`tei.dh.unibe.ch`: the egress proxy refuses the CONNECT, only ports 80 and 443
are open on the box, and neither can be changed. MCP is the exception, and not by
accident — it does not travel the session's egress path at all, it is dialled by
Anthropic's broker. tei already serves four read-only corpus servers that way
(`knowledge_hub/mcp_registry.py`). This adds a fifth that can *do* something,
behind the same nginx on the same 443.

See `docs/CLAUDE_CODE_CONNECTIVITY.md` for how that was established.

**What it deliberately is not.** Not a shell and not an interpreter. Every tool
is either read-only or starts one fixed command whose arguments have been checked
against a charset and a root directory (`mcp_atr/jobs.py`). A caller chooses
*which* corpus and *which* models; it can never choose a command, a path outside
the corpus roots, or a flag.

It does hand back file contents — `read_pages` returns transcriptions — and that
is the one place worth being exact about. It reads ``.txt`` files under
``VLM_TEST_ROOT/<run>/<model>/``, and the run and model names are validated
before the join while every page path is checked *after* resolution to still be
inside that directory. So it is a reader of what this stack itself wrote, not a
file server: there is no argument that reaches the rest of the disk.

That restraint is load-bearing rather than tasteful. The corpus servers next door
are read-only, so a stolen token there costs a public-domain lexicon. Here it
would reach a machine with two A40s, so the blast radius of the token is exactly
the set of tools below — which is why the set is short and why none of them takes
free text that becomes a command.

**One hop further: UBELIX** (#674). The ``ubelix_*`` tools ssh from tei to the
Slurm cluster, read-only, with a key that UBELIX itself binds to one script
(``deploy/ubelix-ro/atr-mcp-ro``). A stolen token reaches the queue and the
trainer's records there, and nothing that submits, cancels or writes.

**Auth.** OAuth, because the claude.ai web connector cannot send anything else:
its dialog takes a URL and, under Advanced settings, an OAuth client id and
secret. Given a 401 it discovers the protected-resource metadata, discovers the
authorization server, registers itself and runs authorization-code with PKCE —
and against a server without OAuth it fails at the first step, which is what it
did here on 2026-09-15.

The MCP SDK implements every endpoint of that. What it cannot supply is who is
allowed in: that is `mcp_atr/oauth.py`, one shared password guarding the
`/authorize` step. Without a login an authorization server hands tokens to
whoever asks, which is an open door with extra steps.

``ATR_MCP_TOKEN`` still works, for clients that *can* send a header — the Claude
Code CLI takes one with ``--header``. Both paths end at the same tools.

Run it::

    ATR_MCP_PASSWORD=… ATR_MCP_PUBLIC_URL=https://tei.dh.unibe.ch/mcp/atr \
        uvicorn mcp_atr.server:app --host 127.0.0.1 --port 8300

Loopback only — nginx does TLS and the public name. See `deploy/mcp-atr/`.
"""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

_PKG = Path(__file__).resolve().parents[1]
if str(_PKG) not in sys.path:               # same flat-import arrangement as __main__
    sys.path.insert(0, str(_PKG))

import config                                # noqa: E402
from mcp_atr import jobs                     # noqa: E402
from mcp_atr import lineset                  # noqa: E402
from mcp_atr import transkribus              # noqa: E402
from mcp_atr import ubelix                   # noqa: E402
from utils import atr_gpu                   # noqa: E402

#: Per-request budget for the gateway probes in :func:`gateway_models`. Three run
#: in sequence, so this is a third of what the caller waits — and the caller is an
#: MCP broker that gives up at 60 s (`jobs.BROKER_TIMEOUT_S`). At the 120 s this
#: was, two slow probes would already have blown past that.
PROBE_TIMEOUT_S = 10


class ConfigError(RuntimeError):
    """The server is not safe to start."""


def public_base() -> str:
    """The URL this server is reachable at from the outside.

    Every OAuth redirect and every metadata document is absolute and is read by a
    browser on the far side of nginx, so this cannot be derived from the request:
    a wrong value here produces a flow that appears to work and lands the user on
    127.0.0.1.
    """
    url = os.environ.get("ATR_MCP_PUBLIC_URL", "").rstrip("/")
    host = urlsplit(url).hostname or ""
    # https, except on the loopback — which is what RFC 8414 allows and what the
    # SDK's own issuer validation allows, so that the flow can be driven end to
    # end without a certificate. Anything else published over http would put the
    # authorization code on the wire in clear.
    if not (url.startswith("https://")
            or (url.startswith("http://") and host in ("localhost", "127.0.0.1", "[::1]"))):
        raise ConfigError(
            "ATR_MCP_PUBLIC_URL must be the https URL this server is published at, "
            "e.g. https://tei.dh.unibe.ch/mcp/atr (http is allowed on the loopback "
            "only). OAuth redirects and metadata are absolute; they cannot be "
            "guessed from a proxied request."
        )
    return url


def _token() -> str:
    token = os.environ.get("ATR_MCP_TOKEN", "")
    if not token:
        return ""                    # OAuth only; see build_app
    if len(token) < 32:
        raise ConfigError(
            "ATR_MCP_TOKEN is unset or shorter than 32 characters. This endpoint "
            "starts jobs on a GPU host and is reachable from the public internet; "
            "it will not run without a token. Generate one with "
            "`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`."
        )
    return token


#: The password form. Deliberately one file, no assets, no JavaScript: it is
#: reached by a browser mid-redirect and the only thing it has to do is take one
#: field and post it back.
LOGIN_PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ATR operations — sign in</title>
<style>
 body{{font:16px/1.5 system-ui,sans-serif;background:#f6f5f3;color:#1a1a1a;
      display:flex;min-height:100vh;margin:0;align-items:center;justify-content:center}}
 form{{background:#fff;padding:2rem;border-radius:8px;box-shadow:0 1px 3px #0002;max-width:22rem}}
 h1{{font-size:1.1rem;margin:0 0 .25rem}}
 p{{color:#555;font-size:.875rem;margin:0 0 1.25rem}}
 input,button{{font:inherit;width:100%;box-sizing:border-box;padding:.6rem;border-radius:5px}}
 input{{border:1px solid #ccc;margin-bottom:.75rem}}
 button{{border:0;background:#1a1a1a;color:#fff;cursor:pointer}}
 .err{{color:#a11;font-size:.875rem;margin:0 0 .75rem}}
</style>
<form method="post">
  <h1>ATR operations on tei</h1>
  <p>This connector can start recognition jobs on idhefix.</p>
  {error}
  <input type="hidden" name="rid" value="{rid}">
  <input type="password" name="password" placeholder="Password" autofocus required>
  <button type="submit">Sign in</button>
</form>
"""


#: What the gateway is asked, and under which key the answer is filed.
#:
#: Both GPU routes, named for whose cards they are. ``/train/gpu`` was once the
#: only one — the route lives on the trainer's router and carries its prefix,
#: and asking ``/gpu`` cost a 404 in the first live call. Since
#: serving-atr-inference#139 ``/gpu`` answers for the **serving** box's cards,
#: and since 16.09.2026 the two are different machines: idhefix serves, asteraix
#: trains.
GATEWAY_PROBES = (
    ("health", "/health"),
    ("models", "/models"),
    ("gpu_serving", "/gpu"),
    ("gpu_training", "/train/gpu"),
)


def gateway_probe() -> dict:
    """Ask the gateway everything this tool reports, and never lose a probe.

    Each route is asked independently and a failure is filed under its own key:
    a gateway that answers ``/models`` and not ``/gpu`` should still tell a
    caller which models it serves.

    Separate from the tool so it can be tested without standing up a server —
    which is the only reason the reporting bug it fixes was ever shipped.
    """
    import httpx

    headers = {"X-API-Key": config.ATR_API_KEY} if config.ATR_API_KEY else {}
    out: dict = {"gateway": config.ATR_GATEWAY_URL}
    with httpx.Client(timeout=PROBE_TIMEOUT_S, headers=headers) as client:
        for name, path in GATEWAY_PROBES:
            try:
                response = client.get(f"{config.ATR_GATEWAY_URL}{path}")
                out[name] = (response.json() if response.status_code == 200
                             else {"status": response.status_code,
                                   "body": response.text[:800]})
            except Exception as exc:  # noqa: BLE001 — one probe must not lose the others
                out[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def gateway_report() -> dict:
    """The probe, plus the subtraction between its two halves (#471).

    Module-level for the reason :func:`gateway_probe` is: the reporting bug that
    one fixes shipped because the logic sat inside the tool, where standing up a
    server was the only way to look at it. One probe feeds the headroom, so the
    report and the numbers it is computed from cannot disagree.
    """
    probe = gateway_probe()
    probe["headroom"] = atr_gpu.headroom_report(probe)
    return probe


def build_server(provider=None, auth_settings=None):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="atr-ops",
        title="ATR operations (tei / idhefix)",
        instructions=(
            "Drive ATR batch runs on tei. Long work is asynchronous: start_batch "
            "returns a job id immediately, then poll job_status and read job_log. "
            "Iteration is model-major because the gateway's vLLM models are lazy "
            "on one GPU — that is the runner's job, not the caller's. "
            "Always dry-run and then smoke-run (limit 3) a new corpus before a "
            "full run; read the END of a transcription, since a page that hit the "
            "token ceiling comes back as a normal success that stops mid-sentence. "
            "compare_readings puts two runs of the same pages side by side; its "
            "numbers are disagreement, never quality — there is no ground truth "
            "here, and a model that hallucinates fluently disagrees exactly as "
            "much as one that reads badly. "
            "ubelix_queue, ubelix_job, ubelix_record, ubelix_report, ubelix_prepared and ubelix_checkout read UBELIX's "
            "Slurm queue, the trainer's records and their evaluation reports there; "
            "they cannot submit or cancel. "
            "A Transkribus volume (export ground truth, fine-tune kraken, evaluate, "
            "read the rest, post it back) runs through transkribus_gt_export, "
            "upload_hf, kraken_train, transkribus_pipeline and transkribus_status, "
            "in that order — docs/TRANSKRIBUS_PIPELINE_MCP.md. Measure with step "
            "'eval' before a full run, and run once without upload."
        ),
        auth_server_provider=provider,
        auth=auth_settings,
    )

    if provider is not None:
        from starlette.responses import HTMLResponse, RedirectResponse

        @server.custom_route("/login", methods=["GET", "POST"])
        async def login(request):                       # noqa: ANN001, ANN202
            """The one step the SDK cannot supply: is this person allowed in.

            Registered with ``custom_route``, which is explicitly exempt from the
            bearer requirement — it has to be, since it is how a bearer token is
            obtained in the first place.
            """
            rid = (request.query_params.get("rid")
                   if request.method == "GET"
                   else (await request.form()).get("rid", ""))

            # SEC-8 (#579): render the form only for a real, pending sign-in. An
            # arbitrary or expired rid never reaches the HTML — so a foreign form
            # cannot make this origin render attacker markup around a password
            # field, even before the escaping below.
            if provider.pending(str(rid)) is None:
                return HTMLResponse(
                    "<p>No pending sign-in request. Start again from the connector.</p>",
                    status_code=400)

            error = ""
            status = 200
            if request.method == "POST":
                remaining = provider.login_locked_for()
                if remaining > 0:
                    # SEC-8: throttle the password form guarding the GPUs.
                    error = ('<p class="err">Too many attempts. Try again in '
                             f'{int(remaining) + 1}s.</p>')
                    status = 429
                else:
                    form = await request.form()
                    if provider.password_ok(str(form.get("password", ""))):
                        provider.note_login_success()
                        target = provider.complete_login(str(rid))
                        if target:
                            return RedirectResponse(target, status_code=302)
                        # Same wording whether the password was wrong or the
                        # request expired: telling them apart tells an attacker
                        # which half to work on.
                        error = '<p class="err">Wrong password, or the request expired.</p>'
                        status = 401
                    else:
                        provider.note_login_failure()
                        error = '<p class="err">Wrong password, or the request expired.</p>'
                        status = 401

            # SEC-8: escape rid before it goes into value="…" (reflected XSS).
            return HTMLResponse(
                LOGIN_PAGE.format(rid=html.escape(str(rid), quote=True), error=error),
                status_code=status)


    @server.tool()
    def gateway_models() -> dict:
        """Model ids the ATR gateway will actually serve, its health, and both
        machines' cards.

        Registration is not servability: a model can be in the registry and
        disabled on this host. This is the list a batch may name.

        **Two GPU reports, named for whose they are.** ``/train/gpu`` was once
        the only one, and the comment here said so: the route lives on the
        trainer's router and carries its prefix, and asking ``/gpu`` cost a 404.
        Since serving-atr-inference#139 that is no longer the whole story —
        ``/gpu`` answers for the **serving** box's cards, and ``/train/gpu`` for
        the trainer's, which since 16.09.2026 are a different machine
        (idhefix serves, asteraix trains).

        Reporting only the trainer's was worse than reporting none. On
        2026-09-21 a batch died with "GPU 1 has 9742 MB free, needs 15848 MB"
        while this tool showed two idle A40s — the trainer's, idle and
        irrelevant — and the one card that mattered could not be seen from here
        at all. ``gpu_serving`` is the one a batch runs on; the old key name
        ``gpu`` is gone rather than redefined, because a key that silently
        changes meaning is how this was missed in the first place.

        **And the subtraction between them.** ``headroom`` answers, per model,
        the question the two reports only made answerable by hand: does it fit
        on its card right now, and if not, by how much and what is holding the
        memory — split into ``ours`` (the ``atr-*`` units, the only rows that
        could ever be asked to give memory back) and ``theirs`` (the
        neighbours' RAG workers on card 0, which are never an option). A model
        whose size or card the registry does not state is ``fits: null``, never
        ``true``: this is a preflight, and a cheerful default would defeat it
        (#471).
        """
        return gateway_report()

    @server.tool()
    def server_status(counts: bool = False) -> dict:
        """Which code answers, which data roots apply, and what lies under
        them. Read-only, synchronous, no secrets (keys and tokens are
        ``set``/``unset``, never values).

        ``code``: the checkout every job runs on — its branch, commit, other
        worktrees, uncommitted files, distance to upstream — and `loaded`
        against `checkout` with ``stale``, which says the server process is
        behind the checkout and needs a restart. On 2026-10-22 the checkout
        stood on a feature branch another workstream had switched to, every
        job ran that branch, and finding out took five terminal commands.

        ``env``: the dotenv files in reading order and whether each exists,
        `AH_ENV_FILE` (which replaces the whole chain), the external reader's
        URL. ``roots``: `VLM_TEST_ROOT`, `GT_ROOT` with its entries — the
        names `keys_from`, `letters_from`, `exclude_from` and `gt_dir` take,
        with line or file counts — the page cache and its cold tier, the
        mount, the staging dir, the research project root. ``runs``: the
        fifty newest runs with pages per model.

        ``resources``: free and total space on `/`, the data directory,
        `ATR_DATASETS_DIR`, `ATR_HF_ARROW_CACHE`, the upload scratch and
        `VLM_TEST_ROOT`; the host's RAM and swap; this service's
        `memory.max`, `memory.high`, `memory.current` and OOM counters — the
        three numbers that decided the BLB uploads of 25.10. (22 GB free
        against ~25 needed, 3 GB RAM, MemoryMax=200M). ``summary`` says it in
        one line each. Read before a big upload.

        ``dependencies``: the runtime requirements in the Python every job
        runs on, with versions, the ones ``missing``, the programs the tools
        call (git, ssh, systemd-run, pagexml-hf) and whether atr-client can
        read on given lines. A missing one also lands in ``summary``. On
        27.10. a job died on ``huggingface_hub`` that nobody had installed.

        ``counts`` walks the page cache and the cold tier for page counts
        (seconds over CIFS, within a budget), and sizes the upload caches
        (leftovers of aborted uploads); off by default.

        Sees, never switches: changing the checkout is a deployment act on
        the machine, not a tool call.
        """
        try:
            return jobs.server_status(counts=counts)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def share_list(folder: Optional[str] = None) -> dict:
        """What is in the Nextcloud share, with sizes. Downloads nothing.

        Also the cheapest check that the share password is right: a wrong one
        fails here in a second rather than an hour into a transfer.

        Answers inline when the listing is quick — `done: true` and the output. A
        large share takes longer than the caller will wait, so past that it comes
        back as `done: false` with a `job_id` to poll. It was a plain synchronous
        call once, and the broker cut it at 60 s while the listing was still
        running on tei: a failure for the caller and work nobody could see.
        """
        return jobs.start_and_peek("share-list", jobs.pull_argv(folder, list_only=True))

    @server.tool()
    def pull_share(folder: Optional[str] = None, limit: Optional[int] = None) -> dict:
        """Mirror a share folder onto tei. Asynchronous — returns a job id.

        Resumable and safe to re-run: a file already present at the remote size is
        skipped, and every download lands through a temp file.
        """
        job = jobs.start("pull-share", jobs.pull_argv(folder, limit=limit))
        return job.as_dict()

    @server.tool()
    def start_batch(models: list[str], run: str, source: str,
                    limit: Optional[int] = None, sample: Optional[int] = None,
                    dry_run: bool = False,
                    concurrency: Optional[int] = None,
                    keys_from: Optional[str] = None,
                    letters_from: Optional[str] = None,
                    exclude_from: Optional[list[str]] = None,
                    keys_out: Optional[str] = None,
                    missing_out: Optional[str] = None,
                    via: str = "gateway", prompt: Optional[str] = None,
                    structured: bool = False, reasoning: Optional[str] = None,
                    no_listing_cache: bool = False) -> dict:
        """Read every page under ``source`` with every model. Returns a job id.

        ``source`` is either a directory — inside the mirror, the comparison
        root, the read-only share mount, the page cache or its cold tier on the
        research share — or ``dav:<folder>``, which reads that folder of the
        Nextcloud share directly, with no mirror and no mount — or
        ``datasets:<tree>/<project>``, one project of a ground-truth tree under
        `ATR_DATASETS_DIR` by name (``datasets:lassberg-blb-gt/lassberg_eval``),
        which is how a dataset's held-out pages are read (#752). Either way a
        source that is not already on local disk is read through a working-copy
        cache, so each page crosses the network once rather than once per
        model. The cold tier (`ATR_PAGE_CACHE_ARCHIVE`) is the source that
        needs **no walk of the share**: it holds a working copy of every page
        some run has read, under the share's own relative path and so under the
        same key, and `keys_from` over it resolves in seconds where the share
        walk takes twenty minutes — and on 2026-10-22 died at a folder that
        answered 404. It holds only what was read once; `missing_out` says what
        it lacks. ``run``
        names the output directory under VLM_TEST_ROOT and is the handle for
        resuming. Re-running the same run skips pages already on disk, so an
        interrupted run is resumed by starting it again — there is no separate
        resume call and no state to reconcile.

        ``limit`` takes the first N pages, ``sample`` takes N at random — they
        are alternatives. On a share that is one folder per document, ``limit``
        gives consecutive pages of a single document: enough to prove the path,
        misleading as an impression of how a model reads the collection. Sampling
        is deterministic, so a resumed run reads the pages the first one did.

        ``keys_from`` names a page-key list written by ``score_ground_truth``'s
        ``keys_out`` — a **name**, not a path, resolved under the ground-truth
        root. With it a run reads only the pages that list names, which is how a
        model reads the pages we can grade instead of the corpus: on 2026-10-02
        the eight candidates over all 6723 pages came to 53,784 calls and about
        two weeks of one card, against 276 pages that have hand-corrected text.
        A key that names no page under ``source`` is reported, never dropped.

        ``no_listing_cache`` re-walks the share instead of reusing the cached
        file list, which is kept for 48 h because a full walk of 6722 files takes
        twenty minutes. Pass it when the question is **what the share holds
        now**: on 2026-10-04 a whole holding had been removed from it, every
        fetch for those pages answered 404, and the cached listing — written the
        day before — still promised them. Without this the stale list was the
        only list reachable from here.

        ``missing_out`` names a file — under the ground-truth root, like
        ``keys_from`` — for the keys this source does **not** have. That list is
        a finding about the corpus rather than about the run: on 2026-10-04 the
        share had stopped holding 35 of the 276 pages we have hand-corrected text
        for, and which 35 was the thing to send to the people who keep it.

        ``letters_from`` selects every page of the letters a file names, and
        ``exclude_from`` (several) leaves out the pages its lists name. Together
        they answer a question the pages cannot: *every page Laßberg wrote that
        nobody has transcribed yet.* The hand is read off a transcription, so an
        untranscribed page cannot supply it — but the edition's register records
        a sender per letter (`letter_register`), and the letter id is in the page
        key. All three are names of files under the ground-truth root, never
        paths. ``keys_out`` writes the resulting selection down, which is what
        makes a run over "the pages nobody has read" repeatable: derived again
        next week, a share that has gained or lost a page gives a different
        selection with nothing saying so.

        The cut is applied **after** every filter, which is the only order that
        gives the number asked for (#531).

        ``via="external"`` reads through an OpenAI-compatible API
        (`GEMINI_BASE_URL`) instead of the gateway, with the versioned
        ``prompt`` whose digest is recorded on every page, and optionally
        ``structured`` for a diplomatic/normalised pair. It returns page text and
        **no line geometry** — there are no polygons in a chat completion — so
        its readings compare and publish but cannot train a line model. It is
        billed per page, so from here it refuses to run without a selection or a
        cut: the gateway's cost is a card that is already paid for and idle, an
        API's lands on an invoice.

        ``reasoning`` is how much the model may think first. Reasoning tokens are
        billed as output *and* spent against the ceiling, so on 2026-10-10 one
        page came back cut off at 1153 characters having spent 8192 tokens, and
        the same page under ``reasoning="low"`` finished in 392. Transcription is
        not a reasoning task. ``""`` sends nothing and leaves the model's own
        default, which is a different request from omitting it.

        Use ``dry_run`` first (it prints pages x models and exits), then
        ``sample=10``, then the whole corpus. ``dry_run`` with
        ``no_listing_cache`` and ``keys_from`` answers how many of those keys the
        share still has, which is the cheapest form of that question.
        """
        try:
            checked_models = jobs.validate_models(models)
            checked_run = jobs.validate_run(run)
            checked_source = jobs.resolve_source(source)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

        if limit is not None and sample is not None:
            return {"ok": False, "error": "limit and sample are alternatives: "
                                          "the first N pages, or N at random"}
        if via == "external" and not (keys_from or letters_from
                                      or limit is not None
                                      or sample is not None):
            return {"ok": False, "error": jobs.EXTERNAL_NEEDS_A_SELECTION}
        try:
            checked_keys = (jobs.resolve_keys_file(keys_from)
                            if keys_from else None)
            checked_letters = (jobs.resolve_letters_file(letters_from)
                               if letters_from else None)
            checked_exclude = [jobs.resolve_keys_file(n)
                               for n in (exclude_from or []) if str(n).strip()]
            checked_keys_out = (jobs.keys_file_to_write(keys_out)
                                if keys_out else None)
            checked_missing = (jobs.keys_file_to_write(missing_out)
                               if missing_out else None)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}
        if checked_missing is not None and checked_keys is None:
            return {"ok": False, "error": "missing_out needs keys_from: the list "
                                          "it writes is the keys that matched "
                                          "nothing, and without a key list there "
                                          "are none"}
        argv = jobs.batch_argv(checked_source, checked_models, checked_run,
                               via=via, prompt=prompt, structured=structured,
                               reasoning=reasoning,
                               letters_from=checked_letters,
                               exclude_from=checked_exclude,
                               keys_out=checked_keys_out,
                               limit=limit, sample=sample, concurrency=concurrency,
                               dry_run=dry_run, keys_from=checked_keys,
                               missing_out=checked_missing,
                               no_listing_cache=no_listing_cache,
                               cache_dir=jobs.cache_dir_for(checked_source))
        if dry_run:
            # The plan is the answer, so it is worth waiting for — but only as
            # long as the caller will, and several thousand pages take longer to
            # enumerate than that.
            return {"ok": True, "dry_run": True, **jobs.start_and_peek(
                "atr-batch-dry-run", argv, run=checked_run)}
        job = jobs.start("atr-batch", argv, run=checked_run)
        return job.as_dict()

    @server.tool()
    def job_status(job_id: Optional[str] = None, limit: int = 10) -> dict:
        """One job, or the most recent ones. Progress is counted from the run's
        own output, which is the same number the runner uses to resume.

        A ``failed`` or ``vanished`` job carries ``memory`` when the service's
        memory cgroup has something to say: its OOM counter before and after
        the job, whether the cgroup's own limit or the host ran out, and the
        other jobs that ran at the same time (the counter is the service's,
        not the job's). ``exited -9`` alone is how a wrong cause was fixed
        first on 25.10."""
        try:
            if job_id:
                return jobs.status(job_id).as_dict()
            return {"jobs": [j.as_dict() for j in jobs.list_jobs(limit)]}
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def job_log(job_id: str, lines: int = 50) -> dict:
        """The tail of a job's output, at most 500 lines."""
        try:
            return {"job_id": job_id, "log": jobs.read_log(job_id, lines)}
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def stop_job(job_id: str) -> dict:
        """Ask a running job to stop (SIGTERM). Finished pages are kept and the
        same start_batch call resumes from there."""
        try:
            return jobs.stop(job_id).as_dict()
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def run_files(run: str, model: Optional[str] = None) -> dict:
        """Which pages a run has read, per model. Returns no text.

        The inventory to ask for first: `read_pages` walks the same keys in the
        same order, so an offset here means the same page there.
        """
        try:
            return jobs.list_outputs(run, model)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def read_pages(run: str, model: str, keys: Optional[list[str]] = None,
                   offset: int = 0, limit: int = 10) -> dict:
        """The transcriptions themselves — one model's reading of a run's pages.

        Without ``keys`` it pages through the model's output from ``offset``;
        with them it reads exactly those. Read the END of each text: a page that
        hit the token ceiling comes back as an ordinary success and stops
        mid-sentence, which `truncated_by_model` is the only warning of.

        Paged deliberately. The reply states what it left out — `remaining`,
        `next_offset`, and `text_cut` on a page too long to carry — so a caller
        copying these somewhere else cannot end up with a silent gap.
        """
        try:
            return jobs.read_outputs(run, model, keys=keys, offset=offset, limit=limit)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def score_ground_truth(runs: list[str], gt_dir: str | None = None,
                           limit: int | None = None,
                           keys_out: str | None = None,
                           writer_agreement: bool = False) -> dict:
        """Score runs' readings against hand-corrected Transkribus pages.

        **The one measurement here that is quality**, not disagreement: the
        ground truth is the reference and the reading the hypothesis, so CER means
        what it normally means. Pages at DONE, FINAL or GT count; below DONE is a
        model's output and scoring against it compares two machines.

        Transkribus shares no identifier with our page keys, so each page is found
        by content. Every match carries its runner-up and whether it is
        `confident`: a best at 20 % against a second-best at 80 % is certain, two
        at 70 % mean the page is not in that run and the number describes nothing.
        Measured on this corpus, two unrelated German pages score about 68 %, so
        a best near that figure is the floor rather than a reading.

        ``keys_out`` names a file to write the **located** pages' keys to, under
        the ground-truth root, for ``start_batch``'s ``keys_from``. That is the
        handover that makes a targeted run possible: only pages whose identity is
        settled, because a wrong key there does not produce a bad number — it
        sends every model to read a different page.

        ``gt_dir`` is a folder under the ground-truth root, or
        ``datasets:<tree>/<project>``: the PAGE XML under that project's
        ``page/`` is the reference. With the same name as `start_batch`'s
        source, the held-out side of a dataset (``…_eval``) is read and scored
        in place (#752). Pages there carry no Transkribus status and count.
        There each page is taken **by name** — ``page/<stem>.xml`` is the
        reading under ``<stem>`` — so a page a model read too badly to be found
        by content stays in its row with its real error instead of dropping
        out as "not located" (#758: 14.0 % reported against 23.0 % over all
        pages on 2026-10-27). A reading without that page is listed under
        ``missing_reading``. The table also gives the character-weighted CER.

        **Asynchronous, like every other long call here.** 552 pages against
        eight readings is about a quarter of an hour, and on 2026-10-05 this tool
        ran it inside the server: the broker cut the request at 60 s, the work
        carried on, and the result reached nobody. It now returns `done` with the
        output when scoring is quick and a `job_id` when it is not — poll
        `job_status`, then `job_log`, which ends with the comparison table
        because the full report goes to the file named in `report`.
        """
        try:
            return jobs.score_job(runs, gt_dir=gt_dir, limit=limit,
                                  writer_agreement=writer_agreement,
                                  keys_out=keys_out)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def letter_register(lassberg: bool = True, sender_gnd: str | None = None,
                        out: str | None = None) -> dict:
        """Who wrote which letter, from the edition's own correspondence data.

        The hand a page is in is inferred everywhere else in this project from
        its dateline. The edition records it: each letter in
        `michaelscho/lassberg` is a TEI file whose `correspDesc` names the sender
        with a GND, plus the place and the date. Measured: 279 letters with a
        sender, 128 of them Laßberg's, four correspondences.

        Synchronous — it reads 280 small local files, so there is no job id.

        With ``out`` it writes the selected letter ids under the ground-truth
        root, by name, and that name is what `start_batch`'s ``letters_from``
        reads back. Identity is the GND, never the spelling: "Laßberg" is also
        written "Lassberg" and "Laspberg".

        It covers 279 of some 3226 letter ids, so it decides where it reaches and
        the dateline inference stays the fallback.
        """
        try:
            return jobs.letter_register_job(sender_gnd=sender_gnd,
                                            lassberg=lassberg, out=out)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def research_list(folder: str | None = None, limit: int = 500) -> dict:
        """What is under a folder of the research project on the research
        share (`ATR_RESEARCH_DIR`, on tei `Projekt_Lassberg`), with sizes.
        Synchronous, one directory deep; ``folder`` is relative to the
        project root and cannot leave it. The HTR training project keeps its
        ground truth and its train/dev/eval manifests here
        (`HTR/data/ground_truth/manifests`), which is what this is for.
        """
        try:
            return jobs.research_list(folder, limit=limit)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def import_ground_truth(manifest: str, name: str,
                            dry_run: bool = False) -> dict:
        """Stage the PAGE XML files a manifest names as a ground-truth set
        under the ground-truth root, by name — the ``gt_dir`` that
        `score_ground_truth` takes, which then writes the key list
        `start_batch` reads. The step before that handover.

        ``manifest`` is a path relative to the research project root, e.g.
        ``HTR/data/ground_truth/manifests/eval_manifest_random.txt``. Its
        lines name files by the mount point of the machine that wrote them;
        they are mapped onto the project root here and read from nowhere
        else. Each copy is named ``<document folder>__<file>``, because
        ``001.xml`` occurs in every document.

        The reply is the finding the shell used to produce by hand: files,
        documents with page counts, the Transkribus status mix (DONE and
        FINAL count as corrected), files without transcribed text, and —
        named, never dropped — the lines that map onto nothing and the files
        the share does not have. Resumable: a copy already present at the
        same size is skipped. ``dry_run`` reports and copies nothing.

        Synchronous: an evaluation set is tens of files. A training split of
        thousands belongs with the trainer, not here.
        """
        try:
            return jobs.import_ground_truth(manifest, name, dry_run=dry_run)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def score_report(name: str, offset: int = 0, limit: int = 50,
                     located_only: bool = False) -> dict:
        """A scoring report as data: the comparison table and every page.

        ``name`` is what `score_ground_truth` returned as ``report_name``
        (``score-gt-<stamp>``; the ``.md``/``.json`` path it printed is
        accepted too). The reply holds ``by_reading`` (the one table that is
        quality), the status mix, the files that could not be scored,
        ``not_located`` — the pages that are in no row, by file name — and
        per page: document and page id, status, the located key, and each
        reading's CER with its runner-up and whether the match is confident.

        Paged like `read_pages`; ``remaining`` and ``next_offset`` say what
        was left out. ``located_only`` drops the pages whose identity is
        unsettled, which is the view to take when reading CERs.

        Reports written before #689 have no data beside them; the error says
        so, and a new `score_ground_truth` writes one.
        """
        try:
            return jobs.score_report(name, offset=offset, limit=limit,
                                     located_only=located_only)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def export_run_share(run: str, model: str | None = None,
                         dest: str | None = None,
                         score_report: str | None = None,
                         keys: str | None = None,
                         dry_run: bool = False) -> dict:
        """A run's readings as text on the research share, for the HTR
        training project: ``<model>/txt/<key>.txt`` and
        ``<model>/records/<key>.json`` per page, the run's ``report.md``, the
        key list the run was started from (``keys``, a name under the
        ground-truth root), a scoring report (``score_report``, the name
        `score_ground_truth` returned) as ``score-gt.md``/``.json``, and a
        README written from the records — model, prompt digest and settings,
        pages per verdict, the empty pages by name, the CER table if a report
        is given, and the sentence that nobody has read this text.

        ``dest`` is a name under ``<ATR_RESEARCH_DIR>/HTR/VLMs``
        (`ATR_RESEARCH_EXPORT_SUBDIR`), default the run's name. Resumable;
        nothing is deleted. ``dry_run`` counts, names the destination and
        returns the README it would write.

        The third exit of a run beside `upload_hf` and `publish-batch`, and
        the only one that reaches the trainer. Refused above 2000 pages: a
        corpus run is a dataset, not a folder of files.
        """
        try:
            return jobs.export_run_share(run, model=model, dest=dest,
                                         score_report=score_report, keys=keys,
                                         dry_run=dry_run)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def export_hf(runs: list[str], gt_dir: str | None = None,
                  dry_run: bool = True) -> dict:
        """Assemble the located ground-truth pages into a `pagexml-hf` export
        tree: one image plus one PAGE XML per page, split into projects.

        It stops at the tree. The upload is `pagexml-hf`, which built every one of
        the fourteen `dh-unibe/image-text_*` datasets, and a second converter here
        would mean a second column layout for the trainer to tolerate. The command
        to run is printed; nothing is uploaded, because publishing a dataset is a
        public act and not a tool call.

        Asynchronous, like scoring, because the plan has to locate every
        ground-truth page in the run first.

        ``dry_run`` defaults to **true**: it prints the plan, the project split
        and the reasons pages were rejected, and writes nothing. Pass false to
        write the tree, which copies an image per page.

        **The geometry check is always on.** A page whose XML geometry disagrees
        with its image crops the wrong strip out of every line, and nothing
        downstream of the dataset would say so. The CLI has a flag to override it
        for someone looking at the pages; this tool does not offer one.

        The project is the *hand*, inherited along each letter: a dateline stands
        on a letter's first page, so the undated pages take the hand their letter
        names. Every page's label, and which letter it came from, is written to
        the TSV the result's ``writers`` names — the plan's counts are a claim
        about an inference, and that file is what makes it checkable.
        """
        try:
            return jobs.export_hf_job(runs, gt_dir=gt_dir, dry_run=dry_run)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def export_run_hf(run: str, model: str | None = None, out: str | None = None,
                      skip_verdicts: list[str] | None = None,
                      keep_truncated: bool = False,
                      dry_run: bool = True) -> dict:
        """Assemble a run's machine readings into a page-wise `pagexml-hf`
        export tree: one image plus one synthesised PAGE document per page, the
        text in a single region covering the page and **no line geometry** —
        a chat completion has none, and inventing strips would be
        indistinguishable from measured ones. The tree records `mode: text` in
        its own manifest, so `upload_hf` cannot be given the wrong mode.

        The sibling of `export_hf`, which exports hand-corrected pages. This
        exports what a recogniser said about pages nobody has transcribed, and
        the dataset it feeds has to say so in its card.

        ``run`` is a run name under VLM_TEST_ROOT (as `batch_report` takes it);
        ``model`` narrows a run that holds several readers. ``out`` is the name
        of the tree to write, under the same root, default ``<run>-pagewise``
        — that name is what `upload_hf` takes as ``tree``. A tree that already
        holds files is refused.

        ``dry_run`` defaults to **true**: it prints the plan — pages per
        project, refusals by category, where the images came from (cache, cold
        tier) — and writes nothing. Pass false to write the tree, which copies
        an image per page.

        Refused by default: pages whose reading came back ``empty``,
        ``repetitive`` (a loop) or ``deliberation`` (the model arguing with
        itself in the transcription field), and pages cut off at the output
        ceiling. ``skip_verdicts`` changes the first set (``short`` can be
        added, ``ok`` never), ``keep_truncated`` the second. Every refusal is
        written to the TSV the result names.

        Source and cold tier come from the configuration, never from here; the
        repository is `upload_hf`'s to name.
        """
        try:
            return jobs.export_run_hf_job(
                run, model=model, out=out, skip_verdicts=skip_verdicts,
                keep_truncated=keep_truncated, dry_run=dry_run)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def upload_hf(tree: str, repo_id: str | None = None,
                  dry_run: bool = True) -> dict:
        """Upload an export tree to the Hugging Face hub with `pagexml-hf`.

        ``tree`` is the **name** of a directory under VLM_TEST_ROOT — the one
        `export_hf` wrote — or ``datasets:<name>`` for one under
        ``ATR_DATASETS_DIR``, where trees too large for the checkout live
        (`server_status` lists them). Never a path. The dry run also counts
        objects per project (the stem before ``_NNNN``), which is what a
        held-out split is drawn over. `pagexml-hf` owns the parquet layout that
        all fourteen `dh-unibe/image-text_*` datasets share; this only hands it
        the tree.

        **Always private.** The CLI can upload without `--private` and asks for a
        second confirmation when it does; this tool cannot, and the flag is absent
        from the command it builds. These are unpublished archival images, and a
        private dataset is the condition under which uploading from a session is
        an unremarkable act.

        **It never removes what the hub holds.** `pagexml-hf` replaces the
        dataset: projects on the hub that are not in the tree would go (the
        Hertwig projects did, 2026-10-24). The upload is then refused and the
        reply names them; export them into the same tree and upload that.

        ``dry_run`` defaults to true and checks what otherwise fails minutes into
        a transfer from inside somebody else's tool: whether the tree is there and
        pairs an image with every XML file, whether `pagexml-hf` is on PATH,
        whether a token is configured — reported as present or absent, never
        printed — and whether the repository id is `owner/name` rather than
        something that would create a repository nobody meant.

        **The real upload runs in its own systemd user unit** (`ah-job-<id>`,
        MemoryMax from `ATR_JOB_MEMORY_MAX`, default 2G), not under this
        server's 200 MB: an OOM there ends the upload, not the server (#737).
        Where no user manager is reachable it is refused, and the reply carries
        the exact command to run it from a shell instead, and the one `sudo`
        line that makes the unit route possible. `job_status` shows the unit.
        """
        try:
            return jobs.upload_hf_job(tree, repo_id=repo_id, dry_run=dry_run)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def split_tree(tree: str, fraction: float = 0.10, seed: int = 20261023,
                   force_eval: Optional[list[str]] = None, undo: bool = False,
                   dry_run: bool = True, allow_after_upload: bool = False) -> dict:
        """Hold out whole **objects** of an export tree before it is uploaded.

        Without ``eval_projects`` the trainer splits pages, and two pages of
        one letter share hand, paper and ink — so a page split reports
        recognition of hands the model trained on. This moves a seeded
        ``fraction`` of each project's objects (the file-name stem before
        ``_NNNN``: ``K2911-47_0001`` → ``K2911-47``) into ``<project>_eval``,
        image and PAGE XML together; train on ``<project>``, measure on
        ``<project>_eval``. ``force_eval`` objects always go to the eval side
        (the ones an earlier measurement used). Idempotent; ``undo`` moves
        everything back. ``tree`` is a name as in `upload_hf`
        (``datasets:<name>`` for ATR_DATASETS_DIR).

        Dry run by default: the result says per project how many objects and
        pages would move. A tree whose ``export.json`` records an upload is
        refused unless ``allow_after_upload`` — the split would change what a
        published revision means. A project whose names carry no object is
        refused rather than split by page.
        """
        try:
            return jobs.split_tree_job(tree, fraction=fraction, seed=seed,
                                       force_eval=force_eval, undo=undo, dry_run=dry_run,
                                       allow_after_upload=allow_after_upload)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def oai_gt_import(source: str, step: str, dry_run: bool = True,
                      limit: Optional[int] = None, workers: int = 4) -> dict:
        """Turn a library's OAI-PMH set of images + ALTO into a ground-truth tree.

        ``source`` is a **name** the server knows (``blb-lassberg``: the
        Badische Landesbibliothek's Laßberg set, Public Domain) — never a URL;
        a new source is a reviewed change to `oai_gt.SOURCES`. Two steps, both
        jobs (poll `job_status`, read `job_log`), both resumable:

        * ``inventory`` — harvest the set's METS (cached under
          ``ATR_DATASETS_DIR/_oai/<source>``), report objects, pages, full
          text and projects, write ``inventory.tsv``.
        * ``build`` — ALTO → PAGE 2013 beside each image, in the source's
          projects (for the BLB the hand, from the catalogue title), under
          ``ATR_DATASETS_DIR/<tree>``. ``dry_run`` (default) plans and
          estimates the size. Pages without one ALTO line are left out. A
          rebuild keeps the split and the upload record in ``export.json``,
          and a new page of a held-out object goes to its ``_eval`` side.

        Then `split_tree` and `upload_hf` with ``tree="datasets:<tree>"``.
        Downloads are throttled and only from the source's own host.

        A real ``build`` runs in its own systemd user unit, as a real
        `upload_hf` does (#737), or is refused with the command to run it by hand.
        """
        try:
            return jobs.oai_gt_import_job(source, step, dry_run=dry_run, limit=limit,
                                          workers=workers)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def transkribus_upload_letters(collection: str, letter: Optional[str] = None,
                                   all_letters: bool = False, dry_run: bool = True,
                                   source: str = "archive") -> dict:
        """Put digitisations into a Transkribus collection, **one document per
        letter** of the edition (``lassberg-letter-NNNN`` with a file under the
        register's ``data/letters``), named by the letter id, with sender,
        register title and repository/signature in the metadata. Images only,
        no text. Pages without a settled letter are counted by reason, never
        uploaded.

        ``collection`` must be one the server allows uploads into
        (``TRANSKRIBUS_UPLOAD_COLLECTIONS``; the ground-truth collection is not
        one). ``source``: ``archive`` (the page cache's cold tier, default) or
        ``cache``. ``dry_run`` (default) logs in and prints the plan. A real
        upload is ``letter=…`` first — look at it in the interface — then
        ``all_letters=true``, which is refused until a letter of the plan is
        in the collection. Letters already there are skipped, so a stopped run
        resumes; every upload is logged with its upload and job id. The
        documents are visible to everyone in the collection at once.
        """
        try:
            return jobs.transkribus_letters_job(collection, letter=letter,
                                                all_letters=all_letters, dry_run=dry_run,
                                                source=source)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def transkribus_collections(name: Optional[str] = None,
                                collection: Optional[str] = None,
                                limit: int = 200, offset: int = 0) -> dict:
        """What the Transkribus account sees. Read-only, synchronous.

        Without ``collection``: the collections whose name contains ``name``
        (``lassberg``), with id and document count — the account sees ~566,
        so filter. With ``collection``: its documents with ``docId``, title,
        pages, status, upload time and the shelfmark read from the title,
        normalised (``done_BLB_K 3123 8 4`` → ``K3123-8-4``), paged by
        ``limit``/``offset``. Answers "what is where, and is it there already"
        before `transkribus_gt_export` or `transkribus_upload_letters`.
        """
        try:
            return jobs.transkribus_collections_job(name=name, collection=collection,
                                                    limit=limit, offset=offset)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def train_spec(spec: str, action: str = "request", job_id: Optional[str] = None) -> dict:
        """A training run from a spec file in ``training/specs/``, any engine.

        ``spec`` is a file's **name** there (``blb-qwen35-page-v1``), never a
        body: the request that runs is the one in the repository. Keys starting
        with ``_`` are notes and are dropped; every dataset whose ``revision``
        is null is pinned to the hub's current commit, and the result says
        which — a null revision reads whatever ``main`` is when the trainer
        fetches.

        ``action``: ``request`` (default) prints the pinned body, sends
        nothing; ``submit`` sends it to the gateway's trainer on asteraix — GPU
        time, so look at ``request`` first — and is refused when the
        ``model_id`` already has a job; ``status`` follows the newest job of
        the model (or ``job_id``); ``ubelix`` prints the commands a person runs
        on UBELIX — `ubelix_*` stay read-only. For ``ubelix`` ``spec`` may name
        several, comma-separated: specs that build the same corpus get one
        build and the others queue behind it. The body is fetched there from
        ``training/requests/`` on main (tei cannot copy to UBELIX); when it is
        not there yet, the output says how to write and commit it first.
        """
        try:
            return jobs.train_spec_job(spec, action, job_id=job_id)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def upload_hf_card(card: str, repo_id: str | None = None,
                       dry_run: bool = True) -> dict:
        """Put a dataset card from `docs/` on the hub as README.md.

        ``card`` is a card's **name** under `docs/` (``hf-dataset-card*.md``),
        never a path. Before the upload the card's front matter is replaced
        by the block the hub README carries — the one `pagexml-hf` wrote on
        upload, with the measured rows, bytes and features. The spliced card
        goes to the job's directory and from there to the hub; `docs/` on
        the server's checkout is **not** written (#738) — a local change
        there stops `update.sh`. Where the card in `docs/` differs from the
        hub, the output says so with the diff, to be committed from a working
        tree. A dataset without a README yet is refused: its sizes are
        measured by the upload, not typed.

        ``dry_run`` (the default) fetches the hub README, shows the splice,
        writes and uploads nothing. The token comes from the configuration
        and never appears in the command; `upload_hf` now also ends by saying
        what the hub holds (rows, bytes, columns, private).
        """
        try:
            return jobs.upload_card_job(card, repo_id=repo_id, dry_run=dry_run)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def compare_readings(runs: list[str], min_chars: int | None = None,
                         worst: int = 10) -> dict:
        """Pairwise disagreement between two or more runs' readings of the same
        pages — the comparison the batch runner never makes.

        **Disagreement, not quality.** Without ground truth nothing here says
        which reading is right, and a model that hallucinates fluently disagrees
        exactly as much as one that reads badly. What it settles is whether
        fusion is worth trying: majority voting was measured LOSING to the best
        single engine where one candidate dominates weaker ones, and winning only
        among comparable candidates whose errors are uncorrelated.

        Only the pages every run has are compared. Pages empty on one side only,
        and pages too short on any side, are counted and kept out of the
        distribution — a five-character fragment against an eight-character one
        disagrees by 75% and says nothing about whether the engines read the hand.
        """
        try:
            return jobs.compare_readings(runs, min_chars=min_chars, worst=worst)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def merge_runs(runs: list[str], run: str, dry_run: bool = False) -> dict:
        """One run holding the best attempt at each page, from several runs.

        ``runs`` is **ordered**: earlier is preferred where the verdicts tie, so
        the canonical run goes first and its retries after it. Pages are chosen
        by verdict — a reading beats a little text, a little text beats nothing,
        and **nothing beats text that is not a reading of the page**
        (`repetitive`, `deliberation`): published in an edition that is
        attributed invention.

        Never by length. A page of the model arguing with itself carries 800
        characters and would win any comparison by size.

        Each merged page keeps the payload of the attempt that produced it and
        gains a ``merged_from`` block naming that attempt and what the others
        said, so a corpus assembled from three runs can still say which page
        came from which.
        """
        try:
            return jobs.merge_runs(runs, run, dry_run=dry_run)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def batch_report(run: str, keys_out: Optional[str] = None) -> dict:
        """The report of a finished (or running) run: what each model produced,
        what it cost, and how many pages were cut off at the token ceiling.

        None of those columns is quality — there is no ground truth in a
        comparison run, and a model that hallucinates fluently leads the
        characters-per-page column.

        ``keys_out`` turns the report's complaints into the next run's input: the
        pages that came back **empty**, **padded** or **cut off** are written as
        a page-key list under the ground-truth root, by name, which
        `start_batch(keys_from=...)` reads back. The keys are recounted from the
        results on disk, not parsed out of `report.md`, because a resumed run's
        report is the one place those counts are known to be short.
        """
        try:
            checked = jobs.validate_run(run)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}
        progress = jobs.run_progress(checked)
        if not progress:
            return {"ok": False, "error": f"no run directory for {checked!r}"}
        report = Path(config.VLM_TEST_ROOT) / checked / "report.md"
        if report.is_file():
            progress["report_md"] = report.read_text(encoding="utf-8")[:40000]
        try:
            progress["flagged"] = jobs.flagged_keys(checked, keys_out=keys_out)
        except jobs.JobError as exc:
            # A report without the selection is still the report that was asked
            # for: the rebuild walks every result file and can fail on a run
            # whose output is half-written, and losing the numbers to that would
            # be the wrong trade.
            progress["flagged"] = {"ok": False, "error": str(exc)}
        return progress

    @server.tool()
    def ubelix_queue() -> dict:
        """This account's Slurm jobs on UBELIX, with Slurm's start estimates.

        Read-only, over ssh from tei with a key that UBELIX binds to one script
        (#674). On ``gpu-invest`` a job can be preempted and requeued several
        times; ``ubelix_job`` shows that history, this only the current state.
        """
        return ubelix.queue()

    @server.tool()
    def ubelix_job(slurm_id: str) -> dict:
        """``sacct -D`` for one UBELIX job: every start, preemption and requeue,
        with node, elapsed time and exit code. Digits only."""
        return ubelix.job(slurm_id)

    @server.tool()
    def ubelix_record(record_id: str, lines: Optional[int] = None) -> dict:
        """The trainer's record for a UBELIX job (status, stage, timestamps,
        ``error``, and ``metrics`` with ``cer``/``wer`` once evaluated) and the
        last lines of its current stage's log, at most 200.

        ``record_id`` is the trainer's job id, e.g.
        ``20261019T180118Z-olmocr2-7b-medieval-page-px4m-native-r16`` — not the
        Slurm id. Slurm only knows the job as ``vlm-train``; which record a Slurm
        id belongs to shows in the timestamps: the record's ``updated_at`` falls
        on the Slurm start.
        """
        return ubelix.record(record_id, lines)

    @server.tool()
    def ubelix_report(record_id: str, label: Optional[str] = None) -> dict:
        """The evaluation reports a UBELIX job holds beside its record.

        Without ``label``: which reports there are (``_`` is the run's own
        ``eval_report.json``; others were written by
        ``ubelix/rescore_on_shared_draw.py``, e.g. ``german-medieval-v1-test``).
        With ``label``: that report — CER, WER, ``length_ratio``,
        ``truncated_cer``, per source and kind, without its example pages — and,
        for a held-out rescore, ``heldout``: which documents the draw and the
        training side share. A score whose ``heldout.shared`` is not empty, or
        whose ``heldout.checked`` is false, is not a held-out number.
        """
        return ubelix.report(record_id, label)

    @server.tool()
    def ubelix_prepared(slurm_id: str) -> dict:
        """What a ``prepare.sbatch`` run on UBELIX left for stage 2.

        ``slurm_id`` is the number ``submit.sh`` printed (``Submitted batch
        job N``). The answer: ``trainer_id`` — the id ``train.sbatch`` takes as
        ``JOB_ID``, read from ``runs/prep-<N>.jobid``, which exists once the
        prepare has **started**; ``slurm`` (state, start, end); ``record``
        (the trainer's status and stage); ``next``, the exact
        ``JOB_ID=… ubelix/submit.sh ubelix/train.sbatch …`` line the prepare's
        log ends with once the corpus is ready; and its last artefact line
        (built or adopted). Read-only: the person runs ``next``.
        """
        return ubelix.prepared(slurm_id)

    @server.tool()
    def ubelix_checkout() -> dict:
        """The trainer checkout on UBELIX (``~/training-atr-models``) against
        ``origin/main``: HEAD, uncommitted files, commits behind — by ``git
        ls-remote``, which fetches nothing. ``submit.sh`` refuses a checkout
        that is behind or dirty; this says so before anyone tries."""
        return ubelix.checkout()

    # ── a Transkribus volume: ground truth → kraken fine-tune → full read ───

    @server.tool()
    def transkribus_gt_export(doc: str, tree: str, pages: Optional[str] = None,
                              test_pages: Optional[str] = None,
                              prefix: str = "A_V_1443_Hertwig",
                              collection: str = transkribus.DEFAULT_COLLECTION,
                              dry_run: bool = True, segmentation: bool = False,
                              status: Optional[str] = None) -> dict:
        """Export a Transkribus document's transcribed pages as a training tree
        (``VLM_TEST_ROOT/<tree>/<prefix>_train`` and ``_test``, image + PAGE XML
        per page) — the input of `upload_hf`, whose private dataset is what
        `kraken_train` trains on.

        ``pages`` are the transcribed pages (``3-60``), or ``status`` selects
        them (``DONE``: every corrected page), or both; ``test_pages`` are held
        out of training entirely, so `transkribus_pipeline` step ``eval`` can
        measure the model on pages it never saw. Empty lines are dropped, pages
        with fewer than three lines of text are refused and listed. Dry run by
        default: it reads and reports, writes nothing.

        ``segmentation=true`` writes the pages for training a line segmenter
        instead: every line kept, transcribed or not, and the XML beside its
        image. Training that runs on asteraix by hand
        (``scripts/segtrain_kraken.sh``) — the trainer has no segtrain.
        """
        try:
            return transkribus.gt_export_job(doc, pages, tree, test_pages=test_pages,
                                             prefix=prefix, collection=collection,
                                             dry_run=dry_run, segmentation=segmentation,
                                             status=status)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def kraken_train(action: str = "request", model_id: Optional[str] = None,
                     hf_repo: Optional[str] = None, train_project: Optional[str] = None,
                     base_model: Optional[str] = None, lrate: Optional[float] = None,
                     epochs: Optional[int] = None, batch_size: Optional[int] = None,
                     job_id: Optional[str] = None, stage: Optional[str] = None,
                     lines: Optional[int] = None) -> dict:
        """Fine-tune a kraken model on a volume's ground truth through the
        gateway's trainer (``/train/jobs``, asteraix), and follow it.

        ``action``: ``request`` prints the body and sends nothing (default);
        ``submit`` sends it — GPU time, so look at ``request`` first, and never
        submit twice for one ``model_id``; ``list`` shows recent jobs;
        ``status`` (by ``job_id`` or the newest of ``model_id``) gives status,
        stage, epoch, validation accuracy, CER and the promotion verdict;
        ``log`` a stage's log (``stage``: prepare|compile|train|test|register).

        Defaults are the Hertwig run (model ``kraken-bern-hertwig-v1`` from
        ``kraken-cursive_inzigkofen``, lrate 1e-4, 60 epochs). That lrate
        under-trained: v2 at ``lrate=5e-4, epochs=150`` reached 0.233 against
        0.271. Under early stopping ``epochs`` sizes the cosine schedule rather
        than capping the run — set it above the epochs you expect.

        ``train_project`` may be several, ``a,b``: one model on both volumes.
        ``base_model`` is a curated registry id, a Zenodo DOI, or
        ``trained:<model id>`` for a model this trainer made (the trainer itself
        accepts only the file, which ``trained:`` resolves to).

        A model whose ``promoted`` stays False is registered but not served.
        If the reason is "no held-out page" (a rerun on the same data reuses
        the compiled corpus), the gate has to be run on the trainer by hand —
        docs/TRANSKRIBUS_PIPELINE_MCP.md says how; this tool cannot.
        """
        try:
            return transkribus.kraken_train_job(
                action, model_id=model_id, hf_repo=hf_repo, train_project=train_project,
                base_model=base_model, lrate=lrate, epochs=epochs, batch_size=batch_size,
                job_id=job_id, stage=stage, lines=lines)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def transkribus_pipeline(step: str, run: str, doc: Optional[str] = None,
                             pages: Optional[str] = None,
                             collection: str = transkribus.DEFAULT_COLLECTION,
                             extra_model: Optional[str] = None,
                             primary: Optional[str] = None, only_extra: bool = False,
                             line_model: Optional[str] = None,
                             trocr_model: Optional[str] = None,
                             page_model: Optional[str] = None, no_llm: bool = False,
                             upload: bool = False, limit: Optional[int] = None,
                             variants: Optional[str] = None, prior: bool = False,
                             status: Optional[str] = None,
                             replace_machine_text: bool = False,
                             on_page_lines: bool = False,
                             external_model: Optional[str] = None,
                             external_prompt: Optional[str] = None,
                             external_reasoning: Optional[str] = None) -> dict:
        """Read a Transkribus document's untranscribed pages and post the result
        back, or measure the readers on its transcribed ones. Files under
        ``VLM_TEST_ROOT/<run>``; every step resumes where it stopped.

        ``step``:
        * ``survey`` — what ``doc`` holds: statuses, the corrected (DONE) pages,
          which of them still have the recognition a human started from, which
          other pages carry uncorrected machine text, and suggested test pages.
        * ``plan`` — which pages of ``doc`` have no lines, or lines without text.
        * ``eval`` — CER of every reader and of the fusion on transcribed
          ``pages`` (the held-out test pages): page CER and line CER (by
          position), plus rules-only fusion per ``variants`` (default A,B,D).
          ``status="DONE"`` keeps only corrected pages; ``prior=true`` adds
          reader T: the version with text a human started from (Transkribus'
          own HTR, as a rule), scored like the others. Posts nothing, ever.
        * ``run`` — plan, recognize, adjudicate, PAGE XML; posted to
          Transkribus as a new IN_PROGRESS version only with ``upload=true``.
          Do it once without, on ``pages`` or ``limit=3``, and look.
        * ``upload`` — post what ``run`` built (dry run unless ``upload=true``).

        Readers: A line VLM, B TrOCR, C page VLM (defaults: the Rümlang set),
        D ``extra_model`` (a kraken model trained on the volume).
        ``only_extra=true`` reads with D alone — no vLLM, party may stay up;
        ``primary`` (A|B|D) wins where the rules do not decide; ``no_llm``
        leaves out the gpt-oss arbiter. On Hertwig D alone beat every fusion
        (line CER 0.371 against 0.383 rules-only and 0.505 with the arbiter),
        so ``only_extra=true, primary=D, no_llm=true`` was the full run.

        **Re-reading machine text** (A V 1447, 25.10.): ``replace_machine_text``
        (plan, run) plans every page whose current text is HTR output nobody
        edited, or none — DONE/FINAL and pages edited after their HTR are left
        out and counted. ``on_page_lines`` (run, eval) reads on the lines the
        page's current version draws instead of re-segmenting, and writes the
        reading back into that PAGE XML, so Transkribus' polygons, regions and
        reading order stay (kraken models; the gateway must run serving-atr-
        inference with given-line support). In eval it measures the reading
        alone, without the segmentation's losses.

        **External readers** (eval only, #747): ``external_model`` names one or
        more model ids at the external API (`GEMINI_BASE_URL`), comma-separated,
        e.g. ``"gemini-3.8-flash,gemini-4-argon"`` — columns G (or G1, G2 …).
        They read the page image and return page text without geometry, so G
        has page CER, and line CER only where its line count equals the GT's.
        ``external_prompt`` is a file under ``prompts/`` (default
        ``bern_1528.md``); ``external_reasoning`` the thinking level (default
        GEMINI_REASONING, "low"; ``""`` = the model's own). Billed per page and
        preflighted against the API's model list; tokens and truncation are in
        the table. Combine with the gateway readers or set them to "none".

        With A, B or C in the run the vLLM models need the card: stop party on
        idhefix first (`gateway_models` → headroom).
        """
        try:
            return transkribus.pipeline_job(
                step, run, doc=doc, pages=pages, collection=collection,
                extra_model=extra_model, primary=primary, only_extra=only_extra,
                line_model=line_model, trocr_model=trocr_model, page_model=page_model,
                no_llm=no_llm, upload=upload, limit=limit, variants=variants,
                prior=prior, status=status, replace_machine_text=replace_machine_text,
                on_page_lines=on_page_lines, external_model=external_model,
                external_prompt=external_prompt, external_reasoning=external_reasoning)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def external_lineset_eval(run: str, model: str, sample: Optional[int] = None,
                              full: bool = False,
                              dataset: Optional[str] = None, workers: int = 8,
                              prompt: Optional[str] = None,
                              reasoning: Optional[str] = None,
                              pages: bool = False,
                              project: Optional[str] = None) -> dict:
        """An external model (Gemini, Argon) on a published **line** test set,
        scored as the trainer scores its adapters, so the number sits in the
        same table (training-atr-models ``docs/EVAL_SETS.md``).

        Default set: ``dh-unibe/image-text_federal-minutes-testset`` — the
        Federal Council minutes (Bundesratsprotokolle 1848–1903, Hodel & Schoch
        2021), 2,751 Kurrent lines. Default prompt ``federal_minutes_line.md``.

        **Billed per line**: exactly one of ``sample`` (that many lines, spread
        evenly over the set) or ``full=true``. Do a sample first and read its
        report. The model is preflighted against the API's list; readings go to
        ``VLM_TEST_ROOT/<run>/readings_<model>.jsonl`` as they come, so a
        re-run resumes and never pays twice under the same settings.

        The report (end of `job_log`, and ``report_<model>.json``): corpus CER
        (Σ edits / Σ reference chars; an unanswered line counts as empty),
        ``truncated_cer``, ``length_ratio``, the CER over answered lines,
        whitespace-flat, empty answers with their ``finish_reason``, tokens, and
        the most frequent confusions (GT → model).

        ``pages=true`` takes a **page** set instead: the reference is the PAGE
        XML (`xml_content`, the lines' own text), the image a whole scan sent at
        most 3500 px wide, and the report adds the per-page median CER — the
        number pages are compared by. ``project`` reads one project of the
        dataset only (``data/<split>/<project>/``), e.g.
        ``dataset="dh-unibe/image-text_sg-missiven", project="sg-missiven"``.
        Runs in its own unit (whole scans exceed the server's memory limit).
        """
        try:
            return lineset.lineset_job(run, model, sample=sample, full=full, dataset=dataset,
                                       workers=workers, prompt=prompt, reasoning=reasoning,
                                       pages=pages, project=project)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def transkribus_status(run: str) -> dict:
        """A pipeline run's state from its files: planned pages, readings per
        model, adjudicated, PAGE XML, uploaded, errors (first 20), the mean CER
        of its last ``eval``, and its latest job. Synchronous."""
        try:
            return transkribus.pipeline_status(run)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

    return server


def build_app():
    """The ASGI app nginx proxies to.

    Credentials are read **first**, on their own lines. Written as one
    expression, Python evaluates arguments left to right and would build the
    whole server before ever looking at them — nothing reaches a socket either
    way, but "refuses to start without a credential" should mean the first thing
    it does is check.

    Two ways in, both ending at the same tools:

    * **OAuth**, for the claude.ai web connector, which can send nothing else.
      The SDK mounts ``/authorize``, ``/token``, ``/register`` and both metadata
      documents; :mod:`mcp_atr.oauth` supplies the password step.
    * **A static bearer token**, for clients that can set a header — the Claude
      Code CLI takes one with ``--header``. Kept because it costs one wrapper and
      it is the only thing that works without a browser.

    ``ATR_MCP_TOKEN``, when set, is seeded into the token store as if it had been
    issued here. One place decides whether a request is authorised — the SDK's
    own middleware — rather than a second check bolted in front of the door, and
    a request without a recognised token gets the ``WWW-Authenticate`` carrying
    ``resource_metadata``, which is where OAuth discovery begins.
    """
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
    from mcp.server.transport_security import TransportSecuritySettings

    from mcp_atr.oauth import AtrAuthProvider, password_from_env

    base = public_base()
    provider = AtrAuthProvider(
        public_base=base,
        store_path=Path(config.DATA_DIR) / "mcp_oauth.json",
        password=password_from_env(),
    )
    settings = AuthSettings(
        issuer_url=base,
        resource_server_url=f"{base}/mcp",
        # The connector registers itself; the alternative is pasting a client id
        # into the dialog by hand, which is the fallback its error message
        # suggests and not a nicer first experience.
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
        # Our own tokens carry the resource they were issued for, so the check
        # costs nothing and refuses a token minted for a different resource.
        validate_token_resource=True,
    )

    token = _token()
    if token:
        provider.seed_static_token(token, f"{base}/mcp")

    # DNS-rebinding protection, configured rather than inherited. Left alone,
    # `streamable_http_app()` sees its default `host="127.0.0.1"` and allows only
    # loopback Host headers — behind nginx the header says `tei.dh.unibe.ch`, and
    # every request would come back 421. Testing against 127.0.0.1 hides this
    # completely, which is why it is set explicitly and named here.
    #
    # Turning the protection off would be the other way out and a worse one: it
    # is what stops a hostile page in the operator's browser from reaching this
    # endpoint through a rebound name.
    host = urlsplit(base).netloc
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, f"{host}:*", "127.0.0.1:*", "localhost:*"],
        allowed_origins=[f"https://{host}", f"http://{host}",
                         "http://127.0.0.1:*", "http://localhost:*"],
    )
    return build_server(provider, settings).streamable_http_app(transport_security=security)


app = build_app() if os.environ.get("ATR_MCP_PASSWORD") else None

"""
mcp_atr/server.py — four operations on tei, reachable from a Claude session.

**Why this exists.** A Claude Code session in the cloud cannot open a socket to
`tei.dh.unibe.ch`: the egress proxy refuses the CONNECT, only ports 80 and 443
are open on the box, and neither can be changed. MCP is the exception, and not by
accident — it does not travel the session's egress path at all, it is dialled by
Anthropic's broker. tei already serves four read-only corpus servers that way
(`knowledge_hub/mcp_registry.py`). This adds a fifth that can *do* something,
behind the same nginx on the same 443.

See `docs/CLAUDE_CODE_CONNECTIVITY.md` for how that was established.

**What it deliberately is not.** Not a shell, not an interpreter, not a file
server. Every tool is either read-only or starts one fixed command whose
arguments have been checked against a charset and a root directory
(`mcp_atr/jobs.py`). A caller chooses *which* corpus and *which* models; it can
never choose a command, a path outside the corpus roots, or a flag.

That restraint is load-bearing rather than tasteful. The corpus servers next door
are read-only, so a stolen token there costs a public-domain lexicon. Here it
would reach a machine with two A40s, so the blast radius of the token is exactly
the set of tools below — which is why the set is short and why none of them takes
free text that becomes a command.

**Auth.** A single bearer token from ``ATR_MCP_TOKEN``. The server refuses to
start without one: a deployment accident must fail loudly at boot rather than
quietly serve an open endpoint. Compared in constant time, because a token
comparison that returns early leaks its length and then its content.

Run it::

    ATR_MCP_TOKEN=… uvicorn mcp_atr.server:app --host 127.0.0.1 --port 8300

Loopback only — nginx does TLS and the public name. See `deploy/mcp-atr/`.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Optional

_PKG = Path(__file__).resolve().parents[1]
if str(_PKG) not in sys.path:               # same flat-import arrangement as __main__
    sys.path.insert(0, str(_PKG))

import config                                # noqa: E402
from mcp_atr import jobs                     # noqa: E402

#: Seconds a synchronous helper (share listing, gateway probe) may take. Past
#: this the answer is not worth an MCP call blocked on it.
SYNC_TIMEOUT_S = 120


class ConfigError(RuntimeError):
    """The server is not safe to start."""


def _token() -> str:
    token = os.environ.get("ATR_MCP_TOKEN", "")
    if len(token) < 32:
        raise ConfigError(
            "ATR_MCP_TOKEN is unset or shorter than 32 characters. This endpoint "
            "starts jobs on a GPU host and is reachable from the public internet; "
            "it will not run without a token. Generate one with "
            "`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`."
        )
    return token


class BearerAuth:
    """ASGI middleware: one shared secret, constant-time, before anything else.

    Plain ASGI rather than a framework's auth stack because the requirement is
    one comparison, and because the SDK's auth settings describe an OAuth
    resource server — advertising metadata endpoints that do not exist would be a
    worse answer than a header check.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        provided = b""
        for key, value in scope.get("headers", []):
            if key.lower() == b"authorization":
                provided = value
                break
        if not secrets.compare_digest(provided, self._expected):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                    (b"www-authenticate", b'Bearer realm="atr"')]})
            await send({"type": "http.response.body", "body": b"unauthorized\n"})
            return
        await self.app(scope, receive, send)


def _run_sync(argv: list[str]) -> dict:
    """Run a short command and return its output. Never a shell; never unbounded."""
    try:
        proc = subprocess.run(  # noqa: S603 — argv built by mcp_atr.jobs
            argv, cwd=str(Path(config.BASE_DIR).parent), capture_output=True,
            text=True, timeout=SYNC_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {SYNC_TIMEOUT_S}s", "argv": argv}
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
            "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-4000:]}


def build_server():
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="atr-ops",
        title="ATR operations (tei / asterAIx)",
        instructions=(
            "Drive ATR batch runs on tei. Long work is asynchronous: start_batch "
            "returns a job id immediately, then poll job_status and read job_log. "
            "Iteration is model-major because the gateway's vLLM models are lazy "
            "on one GPU — that is the runner's job, not the caller's. "
            "Always dry-run and then smoke-run (limit 3) a new corpus before a "
            "full run; read the END of a transcription, since a page that hit the "
            "token ceiling comes back as a normal success that stops mid-sentence."
        ),
    )

    @server.tool()
    def gateway_models() -> dict:
        """Model ids the ATR gateway will actually serve, and its health.

        Registration is not servability: a model can be in the registry and
        disabled on this host. This is the list a batch may name.
        """
        import httpx

        headers = {"X-API-Key": config.ATR_API_KEY} if config.ATR_API_KEY else {}
        out: dict = {"gateway": config.ATR_GATEWAY_URL}
        with httpx.Client(timeout=30.0, headers=headers) as client:
            for name, path in (("health", "/health"), ("models", "/models"), ("gpu", "/gpu")):
                try:
                    response = client.get(f"{config.ATR_GATEWAY_URL}{path}")
                    out[name] = (response.json() if response.status_code == 200
                                 else {"status": response.status_code,
                                       "body": response.text[:800]})
                except Exception as exc:      # noqa: BLE001 — one probe must not lose the others
                    out[name] = {"error": f"{type(exc).__name__}: {exc}"}
        return out

    @server.tool()
    def share_list(folder: Optional[str] = None) -> dict:
        """What is in the Nextcloud share, with sizes. Downloads nothing.

        Also the cheapest check that the share password is right: a wrong one
        fails here in a second rather than an hour into a transfer.
        """
        return _run_sync(jobs.pull_argv(folder, list_only=True))

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
                    limit: Optional[int] = None, dry_run: bool = False,
                    concurrency: Optional[int] = None) -> dict:
        """Read every page under ``source`` with every model. Returns a job id.

        ``source`` must lie inside the mirror or the comparison root; ``run``
        names the output directory under VLM_TEST_ROOT and is the handle for
        resuming. Re-running the same run skips pages already on disk, so an
        interrupted run is resumed by starting it again — there is no separate
        resume call and no state to reconcile.

        Use ``dry_run`` first (it prints pages x models and exits), then
        ``limit=3``, then the whole corpus.
        """
        try:
            checked_models = jobs.validate_models(models)
            checked_run = jobs.validate_run(run)
            checked_source = jobs.resolve_source(source)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}

        argv = jobs.batch_argv(checked_source, checked_models, checked_run,
                               limit=limit, concurrency=concurrency, dry_run=dry_run)
        if dry_run:                    # seconds, and the answer is the point
            return {"ok": True, "dry_run": True, **_run_sync(argv)}
        job = jobs.start("atr-batch", argv, run=checked_run)
        return job.as_dict()

    @server.tool()
    def job_status(job_id: Optional[str] = None, limit: int = 10) -> dict:
        """One job, or the most recent ones. Progress is counted from the run's
        own output, which is the same number the runner uses to resume."""
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
    def batch_report(run: str) -> dict:
        """The report of a finished (or running) run: what each model produced,
        what it cost, and how many pages were cut off at the token ceiling.

        None of those columns is quality — there is no ground truth in a
        comparison run, and a model that hallucinates fluently leads the
        characters-per-page column.
        """
        try:
            checked = jobs.validate_run(run)
        except jobs.JobError as exc:
            return {"ok": False, "error": str(exc)}
        progress = jobs.run_progress(checked)
        report = Path(config.VLM_TEST_ROOT) / checked / "report.md"
        if report.is_file():
            progress["report_md"] = report.read_text(encoding="utf-8")[:40000]
        return progress or {"ok": False, "error": f"no run directory for {checked!r}"}

    return server


def build_app():
    """The ASGI app nginx proxies to: the MCP transport behind the bearer check."""
    return BearerAuth(build_server().streamable_http_app(), _token())


app = build_app() if os.environ.get("ATR_MCP_TOKEN") else None

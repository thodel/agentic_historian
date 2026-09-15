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

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

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
  <p>This connector can start recognition jobs on asterAIx.</p>
  {error}
  <input type="hidden" name="rid" value="{rid}">
  <input type="password" name="password" placeholder="Password" autofocus required>
  <button type="submit">Sign in</button>
</form>
"""


def build_server(provider=None, auth_settings=None):
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
            error = ""

            if request.method == "POST":
                form = await request.form()
                if not provider.password_ok(str(form.get("password", ""))):
                    # Same page, same wording, whether the password was wrong or
                    # the request had expired: a form that distinguishes them
                    # tells an attacker which half to work on.
                    error = '<p class="err">Wrong password, or the request expired.</p>'
                else:
                    target = provider.complete_login(str(rid))
                    if target:
                        return RedirectResponse(target, status_code=302)
                    error = '<p class="err">Wrong password, or the request expired.</p>'

            if provider.pending(str(rid)) is None and not error:
                return HTMLResponse(
                    "<p>No pending sign-in request. Start again from the connector.</p>",
                    status_code=400)
            return HTMLResponse(LOGIN_PAGE.format(rid=str(rid), error=error),
                                status_code=401 if error else 200)


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
    return build_server(provider, settings).streamable_http_app()


app = build_app() if os.environ.get("ATR_MCP_PASSWORD") else None

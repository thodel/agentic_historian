"""
mcp_propose.py — turn an MCP probe into a reviewed PR (#229, P1-D2).

UI-agnostic core (the bot stays a thin shell, #33). Given a probe report for a
candidate MCP source, it:

  1. checks guardrails (https URL, tools were found, name not already registered),
  2. renders the ``MCPSource(...)`` snippet (``mcp_probe.registry_snippet``) and
     splices it into ``knowledge_hub/mcp_registry.py`` before the SOURCES tuple's
     closing paren,
  3. commits that one file to a deterministic feature branch (``mcp/add-<name>``)
     and opens a PR on the code repo — reusing ``publish_github``'s Git Data API
     helpers.

**The running federation is never modified** — review + merge + deploy is the
only path to activation. All GitHub I/O goes through an injectable
``requests.Session`` so the whole module is offline-testable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import config

# Repo-relative path of the registry file the PR edits.
REGISTRY_REPO_PATH = "agentic_historian/knowledge_hub/mcp_registry.py"
_SOURCES_OPEN = "SOURCES: tuple[MCPSource, ...] = ("


def branch_name(name: str) -> str:
    """Deterministic feature-branch name for a proposed source."""
    return f"mcp/add-{name}"


# ── guardrails ────────────────────────────────────────────────────────────────

def check_guardrails(name: str, url: str, report) -> Optional[str]:
    """Return a human-readable refusal message, or ``None`` if the proposal may
    proceed. Refuses: non-https URL, a probe that found no usable tools, and a
    name that already exists in the live registry."""
    if not (url or "").startswith("https://"):
        return f"⛔ Die URL muss `https://` sein (erhalten: `{url}`)."
    if not (getattr(report, "transport", None) and getattr(report, "tools", None)):
        return f"⛔ Der Probe fand keine nutzbaren Tools an `{url}` — kein Vorschlag."
    from knowledge_hub import mcp_registry
    existing = next((s for s in mcp_registry.SOURCES if s.name == name), None)
    if existing is not None:
        return (f"⛔ Quelle `{name}` existiert bereits (`{existing.url or 'gateway'}`). "
                f"Bearbeite stattdessen den vorhandenen Eintrag in mcp_registry.py.")
    return None


# ── registry patch ────────────────────────────────────────────────────────────

def patch_registry(source_text: str, snippet: str) -> str:
    """Splice ``snippet`` (an ``MCPSource(...)`` block) into ``source_text`` as
    the last element of the SOURCES tuple — i.e. indented and comma-terminated,
    right before the tuple's closing ``)``. Raises if SOURCES isn't found."""
    open_idx = source_text.find(_SOURCES_OPEN)
    if open_idx == -1:
        raise ValueError("SOURCES tuple not found in registry source")
    # The tuple's closing paren is the first line that is exactly ')' (column 0);
    # every MCPSource entry closes indented ('    ),'), so this can't collide.
    close_idx = source_text.find("\n)", open_idx)
    if close_idx == -1:
        raise ValueError("SOURCES closing paren not found")
    indented = "\n".join(("    " + ln) if ln.strip() else "" for ln in snippet.splitlines())
    entry = indented + ",\n"
    insert_at = close_idx + 1                     # after the '\n', before the ')'
    return source_text[:insert_at] + entry + source_text[insert_at:]


def _read_registry() -> str:
    return (Path(__file__).parent / "knowledge_hub" / "mcp_registry.py").read_text(encoding="utf-8")


# ── report rendering ──────────────────────────────────────────────────────────

def format_report(name: str, url: str, report) -> str:
    """Discord/Markdown probe report — also used as the PR body."""
    tool_names = sorted(t.get("name", "?") for t in (report.tools or []))
    lines = [
        f"### MCP-Quelle vorgeschlagen: `{name}`",
        f"- **URL:** `{url}`",
        f"- **Transport:** `{report.transport}`",
        f"- **Server:** `{(report.server_info or {}).get('name', '?')}`",
        f"- **Tools ({len(tool_names)}):** " + (", ".join(f"`{t}`" for t in tool_names) or "—"),
        f"- **Contract (search-persons-like):** "
        + (f"✅ `{report.contract_tool}`" if report.contract else "⚠️ nicht erkannt"),
        f"- **Beispiel-Trefferzahl:** {report.sample if report.sample is not None else '—'}",
    ]
    if report.errors:
        lines.append("- **Fehler:** " + "; ".join(report.errors))
    lines.append("")
    lines.append(f"Branch `{branch_name(name)}` — nach Review mergen und deployen "
                 f"(die laufende Föderation wird nicht verändert).")
    return "\n".join(lines)


# ── the proposal (commit + PR) ────────────────────────────────────────────────

def propose(name: str, url: str, report, *, registry_text: Optional[str] = None,
            session=None, repo: Optional[str] = None, base: Optional[str] = None) -> dict:
    """Guardrail-check, patch the registry, commit to ``mcp/add-<name>`` and open
    a PR. Returns ``{ok: True, pr_url, branch}`` or ``{ok: False, error}``.

    ``registry_text`` / ``session`` are injectable for offline tests.
    """
    err = check_guardrails(name, url, report)
    if err:
        return {"ok": False, "error": err}

    from utils import mcp_probe, publish_github
    snippet = mcp_probe.registry_snippet(name, url, report)
    src = registry_text if registry_text is not None else _read_registry()
    patched = patch_registry(src, snippet)

    repo = repo or config.GITHUB_CODE_REPO
    base = base or config.GITHUB_CODE_BRANCH
    branch = branch_name(name)
    body = format_report(name, url, report)

    publish_github._commit_files(
        {REGISTRY_REPO_PATH: patched.encode("utf-8")},
        f"feat: propose MCP source '{name}' (#220)\n\nProbed {url}; adds the "
        f"MCPSource entry for review. The running federation is unchanged.",
        session=session, repo=repo, branch=branch, base_branch=base,
    )
    pr_url = publish_github.open_pr(
        branch, f"Add MCP source: {name}", body, repo=repo, base=base, session=session,
    )
    return {"ok": True, "pr_url": pr_url, "branch": branch}

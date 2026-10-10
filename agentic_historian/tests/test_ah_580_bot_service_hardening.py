"""Tests for SEC-9 (#580): the bot's systemd unit must be sandboxed like the MCP
server's.

The bot process holds every secret in .env.gpustack at once and ran with none of
the confinement the MCP server unit (deploy/mcp-atr/atr-mcp.service) already has.
SEC-9 adds a hardening drop-in so a bot compromise cannot write outside its data
dir or escalate. This test pins the directives so a later edit cannot quietly
drop them, and checks the bot is hardened at least as much as the MCP server on
the core directives the audit named.

Only the blast-radius part of SEC-9 is code here. Reducing the SET of secrets
(separation from the OpenClaw agent) is tracked in #567; key rotation is an
operator action documented in deploy/systemd/README.md.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DROPIN = REPO_ROOT / "deploy/systemd/agentic-historian.service.d/hardening.conf"
MCP_UNIT = REPO_ROOT / "deploy/mcp-atr/atr-mcp.service"

# The sandboxing directives the audit named, plus the kernel/cgroup/SUID set the
# MCP unit carries. ProtectHome is deliberately NOT required: the bot's checkout
# may live under /home/dh, where read-only would break data/ — the drop-in guides
# the operator instead of shipping a value that can brick the service.
REQUIRED = {
    "NoNewPrivileges": "yes",
    "PrivateTmp": "yes",
    "ProtectSystem": "strict",
    "ProtectKernelTunables": "yes",
    "ProtectControlGroups": "yes",
    "RestrictSUIDSGID": "yes",
    "LockPersonality": "yes",
}


def _active_directives(path: Path) -> dict[str, str]:
    """`Key=Value` for every uncommented directive line in a unit file."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line or line.startswith("["):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def test_the_hardening_dropin_exists():
    assert DROPIN.is_file(), "SEC-9 bot hardening drop-in is missing"


def test_the_dropin_sets_every_required_sandbox_directive():
    active = _active_directives(DROPIN)
    for key, value in REQUIRED.items():
        assert active.get(key) == value, f"{key}={value} missing from the bot hardening drop-in"


def test_the_dropin_narrows_writable_paths():
    """ProtectSystem=strict is only a sandbox if the writable set is narrow —
    an active ReadWritePaths must be present (so everything else is read-only)."""
    active = _active_directives(DROPIN)
    assert "ReadWritePaths" in active and active["ReadWritePaths"], \
        "ProtectSystem=strict needs an explicit ReadWritePaths"


def test_the_bot_is_hardened_at_least_as_much_as_the_mcp_server():
    """Parity: every required directive is also active in the reference MCP unit,
    so this test tracks that reference rather than a hand-made list drifting."""
    mcp = _active_directives(MCP_UNIT)
    for key, value in REQUIRED.items():
        assert mcp.get(key) == value, f"reference MCP unit no longer sets {key}={value}"

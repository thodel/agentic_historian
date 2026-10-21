"""The operator's checklist has to list the switches that are off (#646).

`deploy/systemd/agentic_historian.env.example` is what somebody copies to
`/etc/agentic_historian.env` and works through when setting the bot up. Its header says
non-secret tunables "all have defaults in config.py — set one here only to
override it", and for a threshold that is right. For a feature switch whose
default is **off** it is not: leaving it out does not keep a default, it leaves
a feature not running — and a feature that is not running looks exactly like one
that is broken.

Three were missing when this was written, two of them gating whole features:
`ENABLE_ATR_WATCH` + `ATR_WATCH_CHANNEL_ID` (the training watcher) and
`CREDENTIAL_WATCH_CHANNEL_ID` (the credential watcher). An operator filling the
file from the example got a bot whose watchers silently did not run.

Same shape as the gate-coverage test from SEC-18 (#641): enumerate what the code
actually reads, and require each one to be either on the checklist or on an
explicit allowlist with a reason. A hardcoded list of names would go stale the
first time somebody adds a switch, which is the failure this file exists to stop.
"""

import re
from pathlib import Path

import pytest


CFG = Path("agentic_historian/config.py")
EXAMPLE = Path("deploy/systemd/agentic_historian.env.example")

#: Env keys the example deliberately omits, and why. An entry is a decision
#: somebody wrote down, not a way to silence this test.
NOT_ON_THE_CHECKLIST: dict[str, str] = {}


def _config_src() -> str:
    return CFG.read_text(encoding="utf-8")


def _env_read_switches() -> set[str]:
    """``ENABLE_*`` flags and ``*_CHANNEL_ID`` values that come from the env.

    Read from the source rather than from the imported module, because the
    question is what ``config.py`` *reads*, not what it ended up holding — a
    derived flag holds a value without reading anything.
    """
    src = _config_src()
    found = set()
    for match in re.finditer(
            r'^(ENABLE_[A-Z0-9_]+|[A-Z0-9_]*CHANNEL_ID)\b[^\n=]*=\s*(.+)$',
            src, re.MULTILINE):
        name, rhs = match.group(1), match.group(2)
        # Multi-line right-hand sides: take enough to see a _get( call.
        tail = src[match.end(2):match.end(2) + 200].split("\n#")[0]
        if '_get("' in rhs or '_get("' in tail:
            found.add(name)
    return found


def _derived_switches() -> set[str]:
    """Flags computed from another setting — these must NOT be on the checklist."""
    src = _config_src()
    derived = set()
    for match in re.finditer(r'^(ENABLE_[A-Z0-9_]+)\s*=\s*(.+)$', src, re.MULTILINE):
        name, rhs = match.group(1), match.group(2)
        if '_get("' not in rhs:
            derived.add(name)
    return derived


def _example_keys() -> set[str]:
    return {
        m.group(1)
        for m in re.finditer(r'^([A-Z0-9_]+)=', EXAMPLE.read_text(encoding="utf-8"),
                             re.MULTILINE)
    }


def test_the_example_lists_every_switch_the_code_reads():
    switches = _env_read_switches()
    assert switches, "precondition: config.py reads at least one switch"
    missing = sorted(switches - _example_keys() - set(NOT_ON_THE_CHECKLIST))
    assert not missing, (
        f"not on the operator's checklist: {missing}. Add each to "
        f"deploy/systemd/agentic_historian.env.example with its default and what it turns "
        f"on, or to NOT_ON_THE_CHECKLIST with the reason it may be left out")


def test_the_two_watchers_are_on_the_checklist():
    """Named explicitly because these were the ones actually missing, and
    because a generic test can be satisfied by a rename."""
    keys = _example_keys()
    for wanted in ("ENABLE_ATR_WATCH", "ATR_WATCH_CHANNEL_ID",
                   "CREDENTIAL_WATCH_CHANNEL_ID"):
        assert wanted in keys, f"{wanted} is missing from the checklist"


def test_publishing_is_on_the_checklist():
    """`ENABLE_GITHUB_PUBLISH` defaults to false and gates the entire publishing
    path — catalogue pages, /find links, and /train_publish, which refuses
    outright while it is off."""
    assert "ENABLE_GITHUB_PUBLISH" in _example_keys()


def test_a_derived_flag_is_not_offered_as_a_setting():
    """`ENABLE_CREDENTIAL_WATCH` is `CREDENTIAL_WATCH_CHANNEL_ID is not None`.
    Putting it on the checklist would invite setting it, and setting it does
    nothing at all — a listed knob that is not connected is worse than a missing
    one, because it reads as configured."""
    offered = _example_keys() & _derived_switches()
    assert not offered, (
        f"these are computed from another setting and cannot be set: {sorted(offered)}")


def test_every_allowlist_entry_names_a_key_the_code_reads():
    """A stale allowlist silently widens: an entry for a renamed switch would
    excuse a new one that happens to take the old name."""
    stale = sorted(set(NOT_ON_THE_CHECKLIST) - _env_read_switches())
    assert not stale, f"NOT_ON_THE_CHECKLIST names keys config.py does not read: {stale}"


@pytest.mark.parametrize("key", sorted(NOT_ON_THE_CHECKLIST))
def test_every_allowlist_entry_has_a_reason(key):
    assert len(NOT_ON_THE_CHECKLIST[key]) > 15, (
        f"{key} needs a reason saying why it may be left off the checklist")


def test_a_switch_that_defaults_off_says_so_in_the_example():
    """The value written in the example has to be the real default, or the file
    documents a configuration nobody runs."""
    src = _config_src()
    example = EXAMPLE.read_text(encoding="utf-8")
    for match in re.finditer(
            r'^(ENABLE_[A-Z0-9_]+)\s*=\s*_get\("(?P<key>[A-Z0-9_]+)",\s*"(?P<dflt>true|false)"\)',
            src, re.MULTILINE):
        name, default = match.group(1), match.group("dflt")
        shown = re.search(rf'^{name}=(\S*)', example, re.MULTILINE)
        if shown is None:
            continue                      # covered by the coverage test above
        assert shown.group(1) == default, (
            f"{name} is shown as {shown.group(1)!r} in the example but defaults "
            f"to {default!r} in config.py")

"""SEC-19 (#646): the bot has to say at startup how it is actually gated.

`config.py` claimed the bot "warns at startup" when no role is configured. It
did not. The only warning sat in `_authorised` and fired **per command
invocation**, so whoever put the bot into an open server got no signal until a
member happened to run a gated command and somebody happened to read the log.

The three settings left over from #571 cannot be set by a commit. Whether they
are set can be made impossible to miss, and that is what these tests pin — with
one property above the others: **the row that cannot be measured must never read
as a pass.** That is the defect this epic already removed in four other places
(#119, #595, #631, #633), and a posture report is exactly where it would come
back, because a clean-looking list is the thing a report is tempted to produce.
"""

import ast
from types import SimpleNamespace

import pytest

import bot


BOT_PATH = "agentic_historian/bot.py"
CFG_PATH = "agentic_historian/config.py"

NOTHING = SimpleNamespace()
BASE_ONLY = SimpleNamespace(REQUIRED_DISCORD_ROLE_ID=1, REQUIRED_ADMIN_ROLE_ID=1,
                            DEPLOY_ADMIN_ROLE_ID=None, DISCORD_GUILD_ID=None)
ALL_SET = SimpleNamespace(REQUIRED_DISCORD_ROLE_ID=1, REQUIRED_ADMIN_ROLE_ID=2,
                          DEPLOY_ADMIN_ROLE_ID=2, DISCORD_GUILD_ID=3)


def _row(rows, label_part):
    for label, ok, note in rows:
        if label_part in label:
            return label, ok, note
    raise AssertionError(f"no row matching {label_part!r} in {[r[0] for r in rows]}")


# ── the unmeasurable row ─────────────────────────────────────────────────────

@pytest.mark.parametrize("cfg", [NOTHING, BASE_ONLY, ALL_SET])
def test_the_discord_side_layer_is_never_reported_as_measured(cfg):
    """It acts BEFORE this code and cannot be read from inside this process."""
    _label, ok, _note = _row(bot.security_posture(cfg), "Befehlsberechtigungen")
    assert ok is bot.POSTURE_UNKNOWN
    assert ok is not True and ok is not False, (
        "an unmeasurable setting reported as measured is the whole defect")


@pytest.mark.parametrize("cfg", [NOTHING, BASE_ONLY, ALL_SET])
def test_the_unmeasurable_row_says_so_in_words(cfg):
    _label, _ok, note = _row(bot.security_posture(cfg), "Befehlsberechtigungen")
    assert "nicht prüfbar" in note


def test_the_unmeasurable_row_is_not_counted_as_missing():
    """Three-valued in both directions: it must not read as a pass, and it must
    not read as a failure either — otherwise a fully configured bot reports a
    problem it does not have, and the report stops being read."""
    lines = bot.log_security_posture(ALL_SET)
    assert any("nicht prüfbar" in line for line in lines)
    rows = bot.security_posture(ALL_SET)
    assert [label for label, ok, _ in rows if ok is False] == []


@pytest.mark.parametrize("cfg", [NOTHING, BASE_ONLY, ALL_SET])
def test_the_unmeasurable_row_is_always_shown(cfg):
    """Not hidden when everything else is green: it is the reminder, and it is
    the one layer a commit can never cover."""
    assert any("nicht prüfbar" in line
               for line in bot.format_posture(bot.security_posture(cfg)))


def test_its_mark_is_not_the_mark_of_a_pass():
    shown = bot.format_posture(bot.security_posture(ALL_SET))
    ok_line = [line for line in shown if "REQUIRED_DISCORD_ROLE_ID" in line][0]
    unknown = [line for line in shown if "nicht prüfbar" in line][0]
    assert ok_line.split("]")[0] != unknown.split("]")[0], (
        "the unmeasurable row carries the same mark as a passing one")


# ── the explicit-vs-effective admin role (SEC-5's subtlety) ──────────────────

def test_an_inherited_admin_role_does_not_count_as_explicitly_set():
    """The distinction /update depends on. With only the base role configured,
    the effective admin role is set (inherited) while the explicit one is not —
    and /update reads the explicit one, so it stays locked for everyone."""
    rows = bot.security_posture(BASE_ONLY)
    _l, explicit, note = _row(rows, "ausdrücklich")
    _l2, effective, _n2 = _row(rows, "effektiv")
    assert explicit is False, "an inherited role was reported as explicitly set"
    assert effective is True, "the effective role is set, by inheritance"
    assert "/update" in note and "ALLE" in note, (
        "the consequence has to be named: /update is locked for everyone")


def test_an_inherited_admin_role_is_named_as_inherited():
    _l, _ok, note = _row(bot.security_posture(BASE_ONLY), "effektiv")
    assert "geerbt" in note


def test_an_own_admin_role_is_not_called_inherited():
    _l, _ok, note = _row(bot.security_posture(ALL_SET), "effektiv")
    assert "geerbt" not in note


def test_explicitly_set_admin_role_unlocks_update_in_the_report():
    _l, ok, _n = _row(bot.security_posture(ALL_SET), "ausdrücklich")
    assert ok is True


# ── the measured rows ────────────────────────────────────────────────────────

def test_nothing_configured_reports_every_checkable_setting_as_missing():
    rows = bot.security_posture(NOTHING)
    measured = [(label, ok) for label, ok, _ in rows if ok is not bot.POSTURE_UNKNOWN]
    assert measured, "precondition: there are measurable rows"
    assert all(ok is False for _label, ok in measured)


def test_everything_configured_reports_no_missing_setting():
    rows = bot.security_posture(ALL_SET)
    assert [label for label, ok, _ in rows if ok is False] == []


def test_a_missing_base_role_names_the_fail_closed_consequence():
    _l, ok, note = _row(bot.security_posture(NOTHING), "REQUIRED_DISCORD_ROLE_ID")
    assert ok is False
    assert "fail-closed" in note or "gesperrt" in note, (
        "not setting it does not open the bot — it closes it, and that is the "
        "part an operator misreads as a bug")


def test_the_report_covers_the_settings_the_epic_left_open():
    """#571's open list: base role, explicit admin role, Discord-side
    permissions. Plus DISCORD_GUILD_ID, which is on the same deployment list."""
    labels = " ".join(label for label, _ok, _n in bot.security_posture(NOTHING))
    for wanted in ("REQUIRED_DISCORD_ROLE_ID", "REQUIRED_ADMIN_ROLE_ID",
                   "DISCORD_GUILD_ID", "Befehlsberechtigungen"):
        assert wanted in labels, f"the posture report leaves out {wanted}"


# ── the report reaches the operator ──────────────────────────────────────────

def test_the_header_names_what_is_missing():
    import io
    import contextlib

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        bot.log_security_posture(BASE_ONLY)
    printed = out.getvalue()
    assert "REQUIRED_ADMIN_ROLE_ID" in printed.splitlines()[0], (
        "the first line has to name the missing setting, not just that one is "
        "missing")


def test_the_report_goes_to_the_console_not_only_the_log():
    """Starting the service is where the operator looks; a line only in the log
    is a line nobody reads while deploying."""
    import io
    import contextlib

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        bot.log_security_posture(NOTHING)
    printed = out.getvalue()
    assert "REQUIRED_DISCORD_ROLE_ID" in printed
    assert "nicht prüfbar" in printed


def test_a_missing_setting_is_logged_at_warning_not_info():
    from loguru import logger

    seen: list[str] = []
    sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        bot.log_security_posture(NOTHING)
    finally:
        logger.remove(sink)
    assert any("Sicherheitslage" in s for s in seen), (
        "a missing security setting has to be a warning, not an info line")


def test_a_complete_posture_is_not_logged_as_a_warning():
    from loguru import logger

    seen: list[str] = []
    sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        bot.log_security_posture(ALL_SET)
    finally:
        logger.remove(sink)
    assert not any("Sicherheitslage" in s for s in seen), (
        "warning on a correct configuration trains the operator to ignore it")


# ── it is actually called, and cannot stop the bot ───────────────────────────

def test_on_ready_calls_the_posture_report():
    """An AST walk, not a substring: a commented-out call satisfies `in src`,
    which is how the #363 wiring guard passed while the watcher was dead."""
    tree = ast.parse(open(BOT_PATH).read())
    on_ready = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
         and n.name == "on_ready"), None)
    assert on_ready is not None, "on_ready not found"
    called = {
        node.func.id for node in ast.walk(on_ready)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "log_security_posture" in called, (
        "on_ready must call log_security_posture")


def test_the_posture_report_is_never_the_reason_the_bot_fails_to_start(monkeypatch):
    """It is a report. A report that can stop the process is worse than none."""
    tree = ast.parse(open(BOT_PATH).read())
    on_ready = next(n for n in ast.walk(tree)
                    if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                    and n.name == "on_ready")
    guarded = False
    for node in ast.walk(on_ready):
        if not isinstance(node, ast.Try):
            continue
        calls = {c.func.id for c in ast.walk(node)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        if "log_security_posture" in calls and node.handlers:
            guarded = True
    assert guarded, "the call must sit in a try/except"


def test_the_report_survives_a_config_without_any_of_the_names():
    """`getattr(..., None)` throughout: a config module from an older deploy
    that lacks a name must produce a row, not an AttributeError."""
    lines = bot.log_security_posture(SimpleNamespace())
    assert len(lines) == len(bot.security_posture(SimpleNamespace()))


# ── the claim in config.py ───────────────────────────────────────────────────

def test_config_no_longer_claims_a_startup_warning_that_does_not_exist():
    """The comment said the bot "warns at startup" while nothing did. It is
    fixed by making the claim true, not by deleting it — and it now points at
    the function, so the next reader can check."""
    src = open(CFG_PATH).read()
    assert "the bot warns at startup" not in src, (
        "the false claim is back")
    assert "log_security_posture" in src, (
        "the comment should name the function that makes the claim true")

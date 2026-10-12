"""`/train_start` — named presets, admin gate, confirm view (#362).

A training run occupies GPU 1 of a shared box for hours or days. So the command
takes a **name** and a model id, and nothing else: a VGSL spec is 130 characters
where one transposed digit silently trains a different network, and a run that
expensive should be deliberate and repeatable rather than improvised.

Two things this file pins hardest, because both would be easy to get subtly
wrong and neither would show until a run had already spent its hours:

**What is approved is what is sent.** The confirmation card is rendered from the
resolved request body, and the submit sends that same body. A card assembled
separately would eventually describe a run that is not the one submitted.

**A cost figure says whether it was measured.** `corpus_plan.py` in the trainer
states the discipline for the same reason — "every figure a plan reports is an
*estimate* from dataset-card metadata" — and a card that presents an estimate as
a measurement is asking for consent it has not earned.

Offline. The trainer is never called; Discord is mocked.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                 # noqa: E402
import training_presets as tp  # noqa: E402

MODEL = "10.5281/zenodo.123"


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


# ── every preset is a request the trainer can actually read ────────────────

def test_every_preset_carries_the_fields_the_trainer_requires():
    """``TrainRequest`` has no default for these, so a body without them is
    refused on submit rather than at review time.

    The authority is ``training-atr-models/src/atr_training/contracts.py``,
    which cannot be imported from here — hence the named list, and hence this
    test, which is the thing that fails when the contract grows a field.
    """
    for name in tp.names():
        body = tp.resolve(name, MODEL)
        for required in tp.REQUIRED_FIELDS:
            assert required in body, f"{name} lacks {required}"
        assert body["datasets"], name
        assert body["datasets"][0].get("hf_repo"), name


def test_every_preset_is_bounded():
    """``all_projects=True`` requires ``max_pages`` — the trainer's own guard,
    whose message says why: 694 projects, ~6.6 TB, "larger than the disk".
    There is no "everything" preset because there is no disk for one.
    """
    for name in tp.names():
        dataset = tp.resolve(name, MODEL)["datasets"][0]
        if dataset.get("all_projects"):
            assert dataset.get("max_pages"), name
            assert not dataset.get("train_projects"), \
                f"{name}: all_projects and train_projects are mutually exclusive"


def test_no_preset_copies_the_architecture():
    """The VGSL spec stays in the repository that trains with it.

    A preset that carried it would be the second copy of an architecture, free
    to drift from the first — and the drift would be invisible until a curve
    came out wrong. A preset that genuinely needs another network has to say so,
    which is what makes this guard worth failing over.
    """
    for name in tp.names():
        params = tp.resolve(name, MODEL).get("params") or {}
        spec = str(params.get("spec") or "")
        assert "[256,64" not in spec, f"{name} copied KRAKEN_PLUS_SPEC"
        assert not spec, f"{name} sets a spec; say why in the preset if intended"


def test_the_preset_names_are_stable_and_ordered():
    assert tp.names() == ("medieval-smoke", "medieval-10epoch", "medieval-full")


def test_an_unknown_preset_names_the_ones_there_are():
    with pytest.raises(tp.PresetError) as caught:
        tp.get("catmus_medieval")
    for name in tp.names():
        assert name in str(caught.value)


def test_a_missing_model_id_is_refused():
    """Without a target id the trainer does not register the result, so the run
    would cost its hours and leave nothing addressable behind."""
    with pytest.raises(tp.PresetError):
        tp.resolve("medieval-smoke", "   ")


def test_resolve_puts_the_model_id_in_the_body():
    assert tp.resolve("medieval-smoke", MODEL)["model_id"] == MODEL


def test_resolve_hands_out_a_copy():
    """A caller that edited the result would otherwise edit the preset for the
    rest of the process — and the next run would quietly inherit it."""
    first = tp.resolve("medieval-smoke", MODEL)
    first["datasets"][0]["max_pages"] = 999_999
    assert tp.resolve("medieval-smoke", MODEL)["datasets"][0]["max_pages"] != 999_999


def test_the_smoke_preset_is_cheap_enough_to_be_a_smoke_test():
    """Its job is to prove the chain, not to produce a model."""
    smoke = tp.get("medieval-smoke")
    assert smoke.pages is not None and smoke.pages <= 100
    assert (tp.resolve("medieval-smoke", MODEL)["params"]["epochs"]) == 1
    assert smoke.hours is not None and smoke.hours < 1


def test_the_presets_are_ordered_by_what_they_cost():
    costs = [tp.get(n).pages for n in tp.names()]
    assert costs == sorted(costs), "a cheaper preset must come first"


# ── the cost says what kind of number it is ────────────────────────────────

def test_an_estimated_cost_says_it_is_estimated():
    line = tp.get("medieval-10epoch").cost_line()
    assert "geschätzt" in line
    assert "gemessen" not in line


def test_a_measured_cost_would_say_measured():
    """No preset claims this yet — none has been run through this command. The
    property has to exist before the first one can honestly set it."""
    measured = tp.Preset(name="x", what="", pages=1, hours=1.0, measured=True)
    assert "gemessen" in measured.cost_line()
    assert not any(tp.get(n).measured for n in tp.names()), \
        "a preset claiming measured figures must have a run behind it"


def test_an_unknown_cost_is_not_dressed_as_free():
    """``None``, never 0 — "0 pages, 0 hours" reads as "costs nothing", which is
    the opposite of what not knowing means."""
    unknown = tp.Preset(name="x", what="")
    assert unknown.pages is None and unknown.hours is None
    assert not unknown.cost_known
    assert "unbekannt" in unknown.cost_line()


def test_hours_are_rendered_at_a_scale_a_reader_can_act_on():
    assert "min" in tp.Preset(name="x", what="", hours=0.15).cost_line()
    assert "6 h" in tp.Preset(name="x", what="", hours=6.0).cost_line()
    assert "Tage" in tp.Preset(name="x", what="", hours=72.0).cost_line()


def test_thousands_are_grouped_without_eating_the_sentence():
    """The first version applied ``.replace(",", " ")`` to the whole f-string
    and ate the sentence's own commas — the card read "`repo`  alle Projekte
    höchstens 18 000 Seiten"."""
    card = tp.describe("medieval-10epoch", MODEL)
    assert "18" in card and "000 Seiten" in card
    assert "`, alle Projekte, höchstens" in card


# ── the card is the request ────────────────────────────────────────────────

def test_the_card_is_rendered_from_the_resolved_body():
    """What somebody approves has to be what goes out."""
    body = tp.resolve("medieval-10epoch", MODEL)
    card = tp.describe("medieval-10epoch", MODEL)
    assert body["model_id"] in card
    assert body["engine"] in card
    assert body["datasets"][0]["hf_repo"] in card
    assert str(body["params"]["epochs"]) in card


def test_the_card_names_what_the_run_is_for():
    """"medieval-10epoch" does not tell anybody whether to click."""
    assert tp.get("medieval-10epoch").what in tp.describe("medieval-10epoch", MODEL)


def test_the_card_says_the_architecture_is_the_trainers_default():
    """The thing most likely to be wrong is the thing nobody sees, so the card
    says where it comes from."""
    card = tp.describe("medieval-smoke", MODEL)
    assert "KRAKEN_PLUS_SPEC" in card
    assert "nicht hier kopiert" in card


def test_the_card_says_the_run_occupies_the_shared_box():
    assert "GPU 1" in tp.describe("medieval-full", MODEL)


# ── the client sends the resolved body, and nothing else ──────────────────

def test_the_client_submits_the_resolved_body():
    from unittest.mock import MagicMock

    from agent_a.training_client import TrainingClient

    sent = {}

    def _post(path, json=None, **kw):
        sent["path"], sent["json"] = path, json
        answer = MagicMock()
        answer.status_code = 200
        answer.json.return_value = {"id": "j1", "status": "queued"}
        return answer

    client = TrainingClient(base_url="http://x")
    client._client = MagicMock()
    client._client.post.side_effect = _post
    client.create_job("medieval-smoke", MODEL)

    assert sent["path"] == "/train/jobs"
    assert sent["json"] == tp.resolve("medieval-smoke", MODEL)
    # The field the trainer has no slot for must not be sent.
    assert "preset" not in sent["json"]


def test_the_one_shot_helper_closes_its_client(monkeypatch):
    """A client entered without being exited leaks its connection pool — which
    is why the command calls a plain function in a thread rather than opening a
    context manager by hand."""
    import agent_a.training_client as tc

    closed = []

    class _Client(tc.TrainingClient):
        def __exit__(self, *a):
            closed.append(True)
            return super().__exit__(*a)

        def submit(self, request):
            return "job"

    monkeypatch.setattr(tc, "TrainingClient", _Client)
    assert tc.submit_training_job({"model_id": "m"}) == "job"
    assert closed == [True]


# ── the command: admin, confirm, and the fixed view pattern ──────────────

def _bot():
    import bot
    return bot


def test_the_command_is_admin_gated_not_role_gated():
    """It spends a shared resource for hours — the same reason `/update` is
    admin-only rather than role-gated."""
    source = (PKG / "bot.py").read_text(encoding="utf-8")
    block = source[source.index('name="train_start"'):]
    decorators = block[:block.index("async def")]
    assert "@admin_only" in decorators
    assert "@require_role" not in decorators


def test_the_command_goes_through_a_confirm_view():
    source = (PKG / "bot.py").read_text(encoding="utf-8")
    block = source[source.index("async def train_start_cmd("):]
    block = block[:block.index("\ndef main")]
    assert "_TrainStartView" in block
    # Nothing is submitted by the command itself; only the confirm callback does.
    assert "submit_training_job" not in block


def test_the_confirm_view_is_persistent_and_recorded():
    """The fixed pattern (#614), not the timed view `/update` used to have — a
    training confirmation is exactly the kind that sits unanswered while
    somebody checks whether the box is free, and a restart in between must not
    kill it.
    """
    import asyncio

    async def go():
        bot = _bot()
        return bot._TrainStartView(token="t", requester="1",
                                   preset="medieval-smoke", model_id=MODEL)

    view = asyncio.run(go())
    assert view.timeout is None
    for item in view.children:
        assert getattr(item, "custom_id", "").startswith("train_start:")


def test_a_stored_training_confirmation_rebuilds_its_view():
    import asyncio

    import pending_confirm

    entry = pending_confirm.Pending(
        token="t", kind="train_start", channel_id="1", message_id="2",
        requester="3", data={"preset": "medieval-full", "model_id": MODEL})

    async def go():
        bot = _bot()
        return bot, bot._view_for_confirm(entry)

    bot, view = asyncio.run(go())
    assert isinstance(view, bot._TrainStartView)
    assert view.preset == "medieval-full" and view.model_id == MODEL


def test_the_choices_match_the_presets():
    """The decorator runs at import and cannot read the module, so the literal
    and the registry are held together by this test — the same arrangement as
    `_RESTARTABLE_CHOICES` and `atr_engines.RESTARTABLE`."""
    import bot
    assert tuple(bot._TRAIN_PRESET_CHOICES) == tp.names()


def test_a_cancelled_confirmation_submits_nothing():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    import pending_confirm

    pending_confirm.add(pending_confirm.Pending(
        token="t", kind="train_start", channel_id="1", message_id="2",
        requester="9", data={"preset": "medieval-smoke", "model_id": MODEL}))
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    async def go():
        view = _bot()._TrainStartView(token="t", requester="9",
                                      preset="medieval-smoke", model_id=MODEL)
        await view.cancel.callback(interaction)

    asyncio.run(go())
    assert pending_confirm.lookup("t")[0] == pending_confirm.MISSING
    said = interaction.response.edit_message.call_args[1]["content"]
    assert "nichts gestartet" in said


def test_an_expired_confirmation_starts_nothing_and_says_why():
    import asyncio
    import time
    from unittest.mock import AsyncMock, MagicMock

    import pending_confirm

    old = pending_confirm.Pending(
        token="t", kind="train_start", channel_id="1", message_id="2",
        requester="9", data={"preset": "medieval-full", "model_id": MODEL})
    old.created_at = time.time() - pending_confirm.TTL_S - 600
    pending_confirm.save([old])

    interaction = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    async def go():
        view = _bot()._TrainStartView(token="t", requester="9",
                                      preset="medieval-full", model_id=MODEL)
        await view.confirm.callback(interaction)

    asyncio.run(go())
    [said] = [c.args[0] for c in interaction.followup.send.call_args_list]
    assert "abgelaufen" in said and "/train_start" in said


def test_a_trainer_refusal_is_shown_with_its_detail():
    """A 507 names the full filesystem and a 409 says the job is already
    terminal. Collapsing those into "training failed" throws away the sentence
    that says what to do (#361)."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    import agent_a.training_client as tc
    import pending_confirm

    pending_confirm.add(pending_confirm.Pending(
        token="t", kind="train_start", channel_id="1", message_id="2",
        requester="9", data={"preset": "medieval-smoke", "model_id": MODEL}))

    def _boom(body):
        raise tc.TrainingClientError(
            "POST /train/jobs failed: 507 — no space left on /scratch")

    interaction = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    async def go():
        bot = _bot()
        import agent_a.training_client as _tc
        _tc.submit_training_job = _boom
        view = bot._TrainStartView(token="t", requester="9",
                                   preset="medieval-smoke", model_id=MODEL)
        await view.confirm.callback(interaction)

    original = tc.submit_training_job
    try:
        asyncio.run(go())
    finally:
        tc.submit_training_job = original

    [said] = [c.args[0] for c in interaction.followup.send.call_args_list]
    assert "507" in said and "/scratch" in said


def test_a_started_run_reports_its_job_id_and_status():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    import agent_a.training_client as tc
    import pending_confirm

    pending_confirm.add(pending_confirm.Pending(
        token="t", kind="train_start", channel_id="1", message_id="2",
        requester="9", data={"preset": "medieval-smoke", "model_id": MODEL}))

    job = MagicMock()
    job.id = "20261012-kraken-smoke"
    job.status = "queued"
    job.queued_reason = "GPU 1 belegt"

    interaction = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    original = tc.submit_training_job
    try:
        tc.submit_training_job = lambda body: job

        async def go():
            view = _bot()._TrainStartView(token="t", requester="9",
                                          preset="medieval-smoke", model_id=MODEL)
            await view.confirm.callback(interaction)

        asyncio.run(go())
    finally:
        tc.submit_training_job = original

    [said] = [c.args[0] for c in interaction.followup.send.call_args_list]
    assert job.id in said
    assert "queued" in said
    assert "GPU 1 belegt" in said          # queued_reason, when there is one
    # The record is gone, so a second click cannot start the run twice.
    assert pending_confirm.lookup("t")[0] == pending_confirm.MISSING


def test_the_confirmation_belongs_to_whoever_asked():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    other = MagicMock()
    other.user.id = 222
    other.response.send_message = AsyncMock()

    async def go():
        view = _bot()._TrainStartView(token="t", requester="111",
                                      preset="medieval-smoke", model_id=MODEL)
        return await view.interaction_check(other)

    assert asyncio.run(go()) is False


# ── the preset values are research decisions, not code decisions ──────────

def test_the_module_says_where_the_authority_for_each_number_is():
    """A preset that trains on the wrong corpus for six hours is worse than no
    preset, so the module has to say which repository decides what."""
    doc = tp.__doc__ or ""
    assert "training-atr-models" in doc
    assert re.search(r"KRAKEN_PLUS_SPEC", doc)

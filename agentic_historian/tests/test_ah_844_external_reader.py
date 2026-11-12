"""#844 — a second reader on the e-codices bench, offline.

The question the second reader answers is whether the dating floor is the
model's or the prompt's, so what matters here is that nothing but the model
changes: the prompt is production's image-only prompt word for word, the pages
go in order and scaled as Agent B scales them, the answer is parsed and grounded
as Agent B's is, and the run file says what it cost and whether it was cut off.

Run from the repo root:
    pytest agentic_historian/tests/test_ah_844_external_reader.py
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                     # noqa: E402
from agents import source_description as agent_b  # noqa: E402
from eval import ecodices_bench as eb             # noqa: E402
from eval import ecodices_external as ex          # noqa: E402

ANSWER = ('{"Beschreibstoff": {"wert": "Pergament"}, "Schrift": {"wert": "Karolingische '
          'Minuskel", "kandidaten": [{"schrift": "Karolingische Minuskel", "zuversicht": 0.8}]}, '
          '"Datierung": {"wert": "9. Jh."}}\n\n## B\nEin Blatt.')


class FakeClient:
    """Records the request and answers like an OpenAI-compatible API."""

    def __init__(self, content=ANSWER, finish_reason="stop"):
        self.calls = []
        self._content, self._finish = content, finish_reason
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **body):
        self.calls.append(body)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content),
                                     finish_reason=self._finish)],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=300))


@pytest.fixture
def pages(tmp_path):
    out = []
    for i in (1, 2):
        p = tmp_path / f"page-{i:04d}.jpg"
        p.write_bytes(b"\xff\xd8\xff")
        out.append(str(p))
    return out


def test_the_second_reader_sends_productions_prompt_and_the_pages_in_order(pages, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_REASONING", "low")
    client = FakeClient()
    describe = ex.describer("gemini-x", client=client)
    result = describe("csg-1-r0", pages, 40)
    body = client.calls[0]
    assert body["model"] == "gemini-x" and body["reasoning_effort"] == "low"
    text, *images = body["messages"][0]["content"]
    assert text["text"] == agent_b.image_only_prompt(pages, None, 40)
    assert [i["type"] for i in images] == ["image_url", "image_url"]
    assert all(i["image_url"]["url"].startswith("data:image/jpeg;base64,") for i in images)
    assert result["source_json"]["Schrift"]["wert"] == "Karolingische Minuskel"
    assert result["source_json"]["Schrift"]["quelle"] == "vlm"
    seen = ["page-0001.jpg", "page-0002.jpg"]
    assert result["source_json"]["Datierung"]["basis"]["seiten"] == seen
    assert result["pages_seen"] == ["page-0001.jpg", "page-0002.jpg"]
    assert result["cost"]["prompt_tokens"] == 1200 and result["model"] == "gemini-x"
    assert result["truncated"] is False
    assert describe.settings["model"] == "gemini-x" and describe.settings["reasoning"] == "low"


def test_a_cut_off_answer_is_marked_not_hidden(pages):
    describe = ex.describer("gemini-x", client=FakeClient(finish_reason="length"), reasoning="")
    assert describe("v-r0", pages, 2)["truncated"] is True


def test_an_empty_reasoning_level_sends_no_parameter(pages):
    client = FakeClient()
    ex.describer("gemini-x", client=client, reasoning="")("v-r0", pages, 2)
    assert "reasoning_effort" not in client.calls[0]


def test_what_would_be_paid_for_nothing_is_refused_before_the_first_page(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        ex.describer("gemini-x")
    with pytest.raises(ValueError, match="reasoning"):
        ex.describer("gemini-x", client=FakeClient(), reasoning="hard")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(ex, "preflight", lambda model, **k: SimpleNamespace(
        refused=True, line="  NO gemini-x: not listed"))
    with pytest.raises(RuntimeError, match="not listed"):
        ex.describer("gemini-x")


def test_the_bench_runs_the_second_reader_and_keeps_the_cost(tmp_path, monkeypatch, pages):
    vol = tmp_path / "v"
    vol.mkdir()
    (vol / "reference.json").write_text('{"material": "pergament", "date": 850}')
    for p in pages:
        (vol / Path(p).name).write_bytes(b"\xff\xd8\xff")
    real = ex.describer
    monkeypatch.setattr(ex, "describer", lambda model, **k: real(model, client=FakeClient(), **k))
    code = eb.main(["run", str(tmp_path), "--runs", "1", "--describe", "external",
                    "--model", "gemini-x", "--reasoning", "low"])
    assert code == 0
    saved = json.loads((vol / "runs" / "00.json").read_text())
    assert saved["model"] == "gemini-x" and saved["cost"]["completion_tokens"] == 300
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["agent_b"] == {"describe": "external", "model": "gemini-x", "reasoning": "low",
                               "max_tokens": config.GEMINI_MAX_TOKENS}
    scores, _ = eb.score(tmp_path)
    assert scores[0].judgements["Datierung"] == {eb.HIT: 1}


def test_the_run_without_a_model_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        eb.main(["run", str(tmp_path), "--describe", "external"])

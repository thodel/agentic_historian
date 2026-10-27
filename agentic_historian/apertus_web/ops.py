"""What the page can do about the model itself: diagnose it, and bring it up.

This is the part of ``deploy/apertus-web/apertus_up.sh`` that needs no root:
the script also (re)installs the page itself, which takes sudo on tei and
which a page that is down cannot do for itself. What remains — is the gateway
there, does it know the model, is there room on the card, start it and wait —
is exactly what a tester needs when the answer to a question is an error.

Every check says what it found in words a tester can act on, and keeps the
gateway's own message beside it: an error that only says "failed" is the one
that costs an afternoon.
"""

from __future__ import annotations

import io
import time
from dataclasses import asdict, dataclass, field

import httpx

#: What the gateway adds to a model's weights before it launches (manager.py:
#: ``vram_mb x 1.15 + 2048``). Kept in step by hand; a wrong value here only
#: changes a warning's wording, never what the gateway does.
HEADROOM_FACTOR = 1.15
HEADROOM_RESERVE_MB = 2048

#: How the gateway's error texts map onto what to do about them. First match
#: wins; the raw detail is always shown as well.
_HINTS = (
    ("exited (code", "vLLM ist beim Start abgestürzt. Den Grund nennt das Log auf idhefix "
                     "(Befehl unten)."),
    ("MB free", "Auf der Karte ist kein Platz. Die Diagnose zeigt, wer den Speicher hält."),
    ("did not become healthy", "Der Kaltstart hat die Startzeit überschritten. Ist das Share "
                               "langsam? Ein zweiter Versuch liest aus dem Cache und ist schneller."),
    ("not a vLLM model", "Das Gateway kennt das Modell nicht als vLLM-Modell: "
                         "Registry auf idhefix prüfen."),
    ("unknown model", "Das Gateway kennt das Modell nicht: läuft idhefix auf main "
                      "(serving-atr-inference#234)?"),
    ("401", "Der Gateway-Schlüssel in /etc/apertus-web.env stimmt nicht."),
)
JOURNAL_HINT = ("auf idhefix: journalctl --user -u atr-gateway --since '20 min ago' "
                "| grep -iE 'Launching vLLM|Error|Traceback|healthy|resident'")


def hint_for(detail: str) -> str:
    for needle, hint in _HINTS:
        if needle in detail:
            return hint
    return "Unerwarteter Fehler. Die Meldung unten und das Log auf idhefix helfen weiter."


@dataclass
class Check:
    id: str
    label: str
    state: str          # ok | warn | error
    detail: str


def _detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail", r.text))[:800]
    except ValueError:
        return r.text[:800]


async def diagnose(client: httpx.AsyncClient, gateway: str, model: str,
                   last_warmup: dict | None = None) -> list[dict]:
    """The checks, in the order a failure would be found; stops where later
    checks could only repeat the first failure."""
    checks: list[Check] = []

    try:
        r = await client.get(f"{gateway}/health", timeout=10)
    except httpx.HTTPError as exc:
        checks.append(Check("gateway", "Gateway erreichbar", "error",
                            f"{gateway} antwortet nicht ({type(exc).__name__}). "
                            "Läuft atr-gateway auf idhefix?"))
        return [asdict(c) for c in checks]
    if r.status_code != 200:
        checks.append(Check("gateway", "Gateway erreichbar", "error",
                            f"/health antwortet {r.status_code}: {_detail(r)}"))
        return [asdict(c) for c in checks]
    health = r.json()
    resident = model in (health.get("resident_models") or [])
    checks.append(Check("gateway", "Gateway erreichbar", "ok",
                        f"{gateway}, {health.get('model_count', '?')} Modelle"))

    r = await client.get(f"{gateway}/models", timeout=10)
    if r.status_code == 401:
        checks.append(Check("key", "Gateway-Schlüssel", "error",
                            "Das Gateway lehnt den Schlüssel ab (401). ATR_API_KEY in "
                            "/etc/apertus-web.env mit /etc/atr-mcp.env vergleichen."))
        return [asdict(c) for c in checks]
    if r.status_code != 200:
        checks.append(Check("key", "Gateway-Schlüssel", "error",
                            f"/models antwortet {r.status_code}: {_detail(r)}"))
        return [asdict(c) for c in checks]
    checks.append(Check("key", "Gateway-Schlüssel", "ok", "angenommen"))

    models = r.json().get("models") or []
    spec = next((m for m in models if m.get("id") == model), None)
    if spec is None:
        checks.append(Check("registered", "Modell registriert", "error",
                            f"{model} fehlt in /models (nicht registriert oder deaktiviert). "
                            "Läuft das Gateway auf idhefix auf main mit "
                            "serving-atr-inference#234?"))
        return [asdict(c) for c in checks]
    checks.append(Check("registered", "Modell registriert", "ok",
                        f"{model}: {spec.get('hf_repo')}, Karte {spec.get('gpu_affinity')}"))

    if resident:
        checks.append(Check("loaded", "Modell geladen", "ok",
                            "geladen, Antworten in Sekunden"))
    else:
        checks.append(Check("loaded", "Modell geladen", "warn",
                            "nicht geladen. Die erste Frage oder «Modell hochfahren» startet "
                            "es, das dauert ca. 6 min."))
        checks.append(await _gpu_check(client, gateway, spec))

    if last_warmup and last_warmup.get("state") == "error":
        checks.append(Check("warmup", "Letztes Hochfahren", "error",
                            f"{last_warmup.get('hint')} | {last_warmup.get('error')}"))
    return [asdict(c) for c in checks]


async def _gpu_check(client: httpx.AsyncClient, gateway: str, spec: dict) -> Check:
    label = "Platz auf der GPU"
    try:
        r = await client.get(f"{gateway}/gpu", timeout=20)
    except httpx.HTTPError as exc:
        return Check("gpu", label, "warn", f"/gpu nicht abrufbar ({type(exc).__name__})")
    if r.status_code != 200:
        return Check("gpu", label, "warn", f"/gpu antwortet {r.status_code}: {_detail(r)}")
    data = r.json()
    gpu = spec.get("gpu_affinity")
    if gpu is None:
        gpu = (data.get("vllm") or {}).get("gpu")
    card = next((c for c in data.get("cards") or [] if c.get("index") == gpu), None)
    if card is None:
        return Check("gpu", label, "warn", f"Karte {gpu} nicht in /gpu")
    free = int(card.get("memory_free_mib") or 0)
    vram = spec.get("vram_mb")
    if not vram:
        return Check("gpu", label, "warn", f"Karte {gpu}: {free} MiB frei; Bedarf unbekannt")
    needed = int(vram * HEADROOM_FACTOR + HEADROOM_RESERVE_MB)
    if free >= needed:
        return Check("gpu", label, "ok", f"Karte {gpu}: {free} MiB frei, {needed} MiB nötig")
    residents = [x.get("id") for x in (data.get("vllm") or {}).get("residents") or []]
    if residents:
        return Check("gpu", label, "warn",
                     f"Karte {gpu}: {free} MiB frei, {needed} MiB nötig. Das Gateway "
                     f"verdrängt dafür {', '.join(residents)}.")
    holders = sorted(card.get("processes") or [], key=lambda p: -int(p.get("used_mib") or 0))
    who = ", ".join(f"{p.get('service') or p.get('command', '?')[:40]} ({p.get('used_mib')} MiB, "
                    f"pid {p.get('pid')})" for p in holders[:3])
    detail = (f"Karte {gpu}: {free} MiB frei, {needed} MiB nötig, und nichts davon kann "
              f"das Gateway verdrängen. Speicher halten: {who or 'unbekannt'}.")
    if any("VLLM" in (p.get("command") or "") for p in holders):
        # 2026-10-26: an EngineCore that outlived its server, unregistered.
        detail += (" Ein VLLM::EngineCore, den das Gateway nicht als geladen führt, ist "
                   "verwaist: auf idhefix mit `kill <pid>` freigeben.")
    return Check("gpu", label, "error", detail)


def warmup_image() -> str:
    """A 256x256 grey PNG as a data URL: the request only has to reach the model."""
    from PIL import Image
    import base64

    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (128, 128, 128)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@dataclass
class Warmup:
    """One bring-up at a time; its state is what the page polls."""
    state: str = "idle"                 # idle | running | ok | error
    started_at: float | None = None
    finished_at: float | None = None
    seconds: float | None = None
    answer: str | None = None
    error: str | None = None
    hint: str | None = None
    log: list[str] = field(default_factory=list)

    def view(self) -> dict:
        out = asdict(self)
        if self.state == "running" and self.started_at:
            out["elapsed"] = round(time.time() - self.started_at)
        if self.state == "error":
            out["journal"] = JOURNAL_HINT
        return out

    def _note(self, text: str) -> None:
        self.log.append(time.strftime("%H:%M:%S ") + text)

    async def run(self, client: httpx.AsyncClient, gateway: str, model: str,
                  timeout_s: float) -> None:
        self.__init__()                 # a fresh attempt forgets the last one
        self.state, self.started_at = "running", time.time()
        self._note(f"Hochfahren von {model} gestartet")
        try:
            r = await client.get(f"{gateway}/health", timeout=10)
            if model in (r.json().get("resident_models") or []):
                self._note("war schon geladen")
            else:
                self._note("nicht geladen: Kaltstart auf idhefix (ca. 6 min)")
            body = {"model": model, "max_tokens": 16, "temperature": 0.0,
                    "messages": [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": warmup_image()}},
                        {"type": "text", "text": "Welche Farbe hat dieses Bild? Ein Wort."}]}]}
            r = await client.post(f"{gateway}/v1/chat/completions", json=body,
                                  timeout=timeout_s)
            if r.status_code >= 400:
                raise RuntimeError(f"Gateway {r.status_code}: {_detail(r)}")
            self.answer = r.json()["choices"][0]["message"]["content"].strip()
            self.state = "ok"
            self._note(f"Modell antwortet: «{self.answer}»")
        except httpx.TimeoutException:
            self.state = "error"
            self.error = f"keine Antwort in {int(timeout_s)} s"
            self.hint = ("Der Kaltstart dauert länger als erwartet. Das Gateway arbeitet "
                         "eventuell noch: in ein paar Minuten «Prüfen».")
        except httpx.HTTPError as exc:
            self.state = "error"
            self.error = f"Gateway nicht erreichbar ({type(exc).__name__}: {exc})"
            self.hint = "Läuft atr-gateway auf idhefix?"
        except Exception as exc:  # noqa: BLE001 — every failure is shown, none swallowed
            self.state = "error"
            self.error = str(exc)[:1500]
            self.hint = hint_for(self.error)
        finally:
            self.finished_at = time.time()
            self.seconds = round(self.finished_at - (self.started_at or self.finished_at), 1)
            if self.state == "error":
                self._note(f"Fehler: {self.error}")

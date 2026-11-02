"""Die Client-Seite der Headroom-Lease (#473).

Das Gateway hält die Lease (serving-atr-inference, ``POST /headroom``): es räumt
die Karte von eigenen, ungenutzten Engines, und **es** lädt sie zurück, wenn die
Lease nicht erneuert wird. Dieser Client kann sie deshalb nur erneuern — und
genau darum ist er sicher: stirbt der Lauf an einem ``ParseError``, an einem
``SIGKILL`` oder an einem verlorenen Netz, läuft die Lease ab, und party ist
wieder oben, ohne dass sich jemand erinnern muss.

Der Herzschlag kommt aus der Fortschrittsschleife, nicht aus einem eigenen
Thread: ein Lauf, der hängt, soll seine Lease verlieren wie einer, der tot ist.
:meth:`HeadroomLease.beat` darf also bei jeder Seite gerufen werden und erneuert
nur, wenn ``heartbeat_s`` vergangen ist.

Was die Lease gekostet hat, hält :meth:`HeadroomLease.record` fest (#474): welche
Engines, wie viele MiB, von wann bis wann, und was das Gateway bei der Freigabe
sagte. ``atr_batch`` legt es als ``lease.json`` neben die Seiten des Modells,
damit ``job_status``, ``report-run`` und ``report.md`` es lesen — auch während
der Lauf noch läuft.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

import config

__all__ = ["HEARTBEAT_S", "OUTCOMES", "TTL_S", "HeadroomClient", "HeadroomLease",
           "HeadroomRefused"]

#: Vorgaben des Issues: Lease 15 min, Herzschlag alle 5.
TTL_S = 900.0
HEARTBEAT_S = 300.0


#: Wie eine Lease endete, wie der Bericht es sagt (#474). Nur ``restored`` heisst,
#: dass das Gateway bestätigt hat, dass jede geräumte Engine wieder läuft.
OUTCOMES = {
    "held": "gehalten — der Lauf läuft noch, oder er starb, bevor er freigab "
            "(dann lädt das Gateway nach Ablauf der TTL zurück)",
    "restored": "zurückgeladen, vom Gateway bestätigt",
    "restoring": "Zurückladen fehlgeschlagen — das Gateway versucht es bei jedem Takt weiter",
    "expired": "abgelaufen, bevor der Lauf freigab — das Gateway hat selbst zurückgeladen; "
               "bestätigt hat der Lauf das nicht",
    "unconfirmed": "Freigabe nicht bestätigt — das Gateway war nicht zu fragen; "
                   "es lädt nach Ablauf der TTL zurück",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HeadroomRefused(RuntimeError):
    """Das Gateway hat keinen Platz gemacht, und sagt warum (Fehlbetrag, Halter)."""

    def __init__(self, message: str, detail: object = None) -> None:
        super().__init__(message)
        self.detail = detail


@dataclass
class HeadroomLease:
    """Eine gewährte Lease, mit dem Herzschlag und der Freigabe."""

    lease_id: str
    model: str
    card: int
    freed: list[dict]
    client: "HeadroomClient"
    heartbeat_s: float = HEARTBEAT_S
    last_beat: float = field(default_factory=time.monotonic)
    #: Gesetzt, wenn das Gateway die Lease nicht mehr kennt (abgelaufen, freigegeben).
    lost: str = ""
    released: bool = False
    #: Uhrzeit (UTC, ISO), nicht ``clock``: der Bericht nennt die Spanne.
    acquired_at: str = field(default_factory=_now)
    released_at: str = ""
    #: Die Antwort des Gateways auf die Freigabe: ``state`` (``ended`` oder
    #: ``restoring``), ``pending``, ``last_error`` — oder warum es keine gab.
    restore: dict = field(default_factory=dict)

    def line(self) -> str:
        freed = " · ".join(f"{f.get('engine')} {f['mib']:,} MiB" if isinstance(f.get("mib"), int)
                           else f"{f.get('engine')}" for f in self.freed)
        return f"lease {self.lease_id} on card {self.card}: freed {freed or 'nothing'}"

    def beat(self) -> None:
        """Erneuern, wenn ``heartbeat_s`` vergangen ist. Wirft nie: ein Herzschlag,
        der scheitert, darf das Lesen nicht anhalten — die TTL ist das Netz darunter."""
        if self.released or self.lost:
            return
        now = self.client.clock()
        if now - self.last_beat < self.heartbeat_s:
            return
        self.last_beat = now
        try:
            self.client.renew(self.lease_id)
        except HeadroomRefused as exc:
            self.lost = str(exc)
            logger.warning("headroom: lease {} for {} is gone ({}) — the gateway has "
                           "restored the card; pages from here may meet a full card",
                           self.lease_id, self.model, exc)
        except Exception as exc:  # noqa: BLE001 — retried at the next beat
            logger.warning("headroom: renewing lease {} failed: {}", self.lease_id, exc)

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.released_at = self.client.now()
        try:
            answer = self.client.release(self.lease_id)
        except Exception as exc:  # noqa: BLE001 — the TTL restores it anyway
            self.restore = {"state": "unconfirmed", "error": str(exc)}
            logger.warning("headroom: releasing lease {} failed ({}); the gateway restores "
                           "the card when the lease expires", self.lease_id, exc)
            return
        self.restore = {k: answer[k] for k in ("state", "pending", "last_error", "detail")
                        if k in answer}
        if self.restore.get("state") == "restoring":
            logger.error("headroom: lease {} released, but {} did not come back ({}); the "
                         "gateway retries on every tick", self.lease_id,
                         self.restore.get("pending"), self.restore.get("last_error"))
        else:
            logger.info("headroom: lease {} for {} released; the gateway restores {}",
                        self.lease_id, self.model, [f.get("engine") for f in self.freed])

    @property
    def outcome(self) -> str:
        """Ein Schlüssel von :data:`OUTCOMES`."""
        if not self.released:
            return "expired" if self.lost else "held"
        state = self.restore.get("state")
        if state == "ended":
            return "restored"
        if state == "restoring":
            return "restoring"
        if state == "gone" or self.lost:
            return "expired"
        return "unconfirmed"

    def record(self) -> dict:
        """Was der Lauf die Karte gekostet hat — der Inhalt von ``lease.json``."""
        return {"lease_id": self.lease_id, "model": self.model, "card": self.card,
                "freed": [dict(f) for f in self.freed],
                "freed_mib": sum(f["mib"] for f in self.freed
                                 if isinstance(f.get("mib"), int)),
                "acquired_at": self.acquired_at, "released_at": self.released_at,
                "outcome": self.outcome, "lost": self.lost, "restore": dict(self.restore)}


class HeadroomClient:
    """``POST /headroom``, renew, release — gegen ``config.ATR_GATEWAY_URL``.

    ``request`` ist die Naht der Tests: ``(method, path, json) -> (status, body)``.
    """

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, *,
                 ttl_s: float = TTL_S, heartbeat_s: float = HEARTBEAT_S,
                 timeout: float = 60.0,
                 request: Optional[Callable[[str, str, Optional[dict]], tuple]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], str] = _now) -> None:
        self.base_url = (base_url or config.ATR_GATEWAY_URL or "").rstrip("/")
        self.api_key = config.ATR_API_KEY if api_key is None else api_key
        self.ttl_s, self.heartbeat_s, self.timeout = ttl_s, heartbeat_s, timeout
        self._request = request or self._http
        self.clock = clock
        self.now = now

    def _http(self, method: str, path: str, body: Optional[dict]) -> tuple[int, object]:
        import httpx

        r = httpx.request(method, self.base_url + path, json=body, timeout=self.timeout,
                          headers={"X-API-Key": self.api_key} if self.api_key else {})
        try:
            payload = r.json()
        except ValueError:
            payload = r.text
        return r.status_code, payload

    @staticmethod
    def _detail(payload: object) -> object:
        return payload.get("detail", payload) if isinstance(payload, dict) else payload

    @staticmethod
    def _reason(detail: object) -> str:
        return detail.get("reason", str(detail)) if isinstance(detail, dict) else str(detail)

    def acquire(self, model: str, holder: str = "") -> Optional[HeadroomLease]:
        """Platz für ``model``. None, wenn es ohne Räumen passt; sonst die Lease.

        Raises :class:`HeadroomRefused` mit dem Grund des Gateways — Fehlbetrag,
        die Karte ist schon geleast, oder ein Gateway ohne ``/headroom`` (404).
        """
        status, payload = self._request("POST", "/headroom",
                                        {"model": model, "ttl_s": self.ttl_s, "holder": holder})
        if status == 200 and isinstance(payload, dict):
            if not payload.get("lease_id"):
                return None
            return HeadroomLease(lease_id=payload["lease_id"], model=model,
                                 card=payload.get("card"), freed=payload.get("freed") or [],
                                 client=self, heartbeat_s=self.heartbeat_s,
                                 last_beat=self.clock(), acquired_at=self.now())
        detail = self._detail(payload)
        if status == 404 and "unknown model" not in str(detail):
            raise HeadroomRefused("this gateway has no /headroom (serving-atr-inference "
                                  "before agentic_historian#473)", detail)
        raise HeadroomRefused(self._reason(detail), detail)

    def check(self, model: str) -> list[dict]:
        """Was das Gateway für ``model`` räumen *würde* — ohne etwas anzufassen.

        Gefragt am Preflight, vor der Auflistung des Shares (#472): ein Modell,
        für das kein Platz zu schaffen ist, fällt dann heraus, statt nach 24
        Minuten Auflistung an der Lease zu scheitern. ``[]`` heisst, es passt
        schon. Raises :class:`HeadroomRefused` — auch, wenn das Gateway gar nicht
        zu fragen war: dann ist die Antwort "nicht", nicht "vielleicht".
        """
        try:
            status, payload = self._request("POST", "/headroom",
                                            {"model": model, "dry_run": True})
        except Exception as exc:  # noqa: BLE001 — unreachable is a no, said why
            raise HeadroomRefused(f"the gateway could not be asked: {exc}") from exc
        if status == 200 and isinstance(payload, dict):
            return list(payload.get("freed") or [])
        detail = self._detail(payload)
        if status == 404 and "unknown model" not in str(detail):
            raise HeadroomRefused("this gateway has no /headroom (serving-atr-inference "
                                  "before agentic_historian#473)", detail)
        raise HeadroomRefused(self._reason(detail), detail)

    def renew(self, lease_id: str) -> None:
        status, payload = self._request("POST", f"/headroom/{lease_id}/renew",
                                        {"ttl_s": self.ttl_s})
        if status in (404, 410):
            raise HeadroomRefused(self._reason(self._detail(payload)))
        if status != 200:
            raise RuntimeError(f"HTTP {status}: {self._reason(self._detail(payload))}")

    def release(self, lease_id: str) -> dict:
        """Freigeben. Die Antwort des Gateways: ``state`` ``ended``, wenn alles wieder
        läuft, ``restoring`` mit ``pending`` und ``last_error``, wenn nicht; ``gone``,
        wenn die Lease schon geendet hatte (410)."""
        status, payload = self._request("DELETE", f"/headroom/{lease_id}", None)
        if status == 410:
            return {"state": "gone", "detail": str(self._reason(self._detail(payload)))}
        if status != 200:
            raise RuntimeError(f"HTTP {status}: {self._reason(self._detail(payload))}")
        return payload if isinstance(payload, dict) else {}

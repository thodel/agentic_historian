"""ontome.py — Schnappschüsse der OntoMe-Profile, die der Bestandsgraph verwendet.

    python -m knowledge_hub.ontology.ontome --status     # was liegt vor
    python -m knowledge_hub.ontology.ontome --refresh    # neu holen, Unterschiede melden

OntoMe (https://ontome.net, LARHRA) ist die Pflegeumgebung von CIDOC CRM und
den SDHSS-Erweiterungen. Die API ist nicht dokumentiert; die Endpunkte und ihre
Parameter stammen aus dem Geovistory-Quellcode (geovistory/toolbox,
``server/src/models/ontome-api/``) und wurden am 14.10.2026 gegen den Dienst
geprüft:

    https://ontome.net/api/profiles.json
    https://ontome.net/api/classes-profile.json?lang=en&available-in-profile=<id>
    https://ontome.net/api/properties-profile.json?lang=en&available-in-profile=<id>

Die Parameter sind mit Bindestrich geschrieben; ``availableInProfile`` wird
stillschweigend ignoriert und liefert ``[]``. Nur **publizierte** Profile
tragen Inhalt — die Wurzelprofile (``isRootProfile``) antworten leer.

Warum Schnappschüsse im Repo: Tests und der Bau müssen offline laufen, und ein
Graph, der heute valide ist, soll es morgen nachprüfbar mit demselben Vokabular
sein. Der Schnappschuss trägt Datum und Profil-Ids; ``--refresh`` schreibt ihn
neu und sagt, welche Begriffe dazukamen oder wegfielen.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

API = "https://ontome.net/api"
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
MANIFEST = SNAPSHOT_DIR / "manifest.json"
BY_ID = SNAPSHOT_DIR / "classes-by-id.json"
SITE = "https://ontome.net"

#: Die publizierten OntoMe-Profile, aus denen das Vokabular stammt. Jedes mit dem
#: Grund, warum es dabei ist — ein Profil ohne Grund ist Ballast, und Ballast
#: macht «bekannter Begriff» zu einer schwächeren Aussage.
PROFILES: dict[int, str] = {
    5:   "Geovistory Basics — CIDOC-Grundstock (E21, E53, E41, E42, E33) und SDHSS Core",
    12:  "Biographical basics and family — Geschlecht, Herkunft, Wohnort (sdh:C15), Verwandtschaft",
    16:  "Interactions, Social Relationships and Memberships — C3 Social Relationship, C5 Membership",
    30:  "Social and legal qualities of a person — C13 Social Role Embodiment, Bürgerrecht, Religion",
    85:  "Description of Sources — F2 Expression, C4 Asserted Actor's Role",
    180: "Person - Social location — sdh:C15 Epistemic Location mit Typ",
    204: "Person - Biological Family — E67 Birth, sdh-slc:C9 Relationship (P20 had partner ×2)",
    208: "Person - Social Relationship — C3 mit Quelle/Ziel/Typ, C43 Rolle in der Beziehung",
    507: "Person - Birth and Death — E67/E69 mit Ort (sdh:C13) und Gebäude",
}


@dataclass(frozen=True)
class OntoClass:
    uri: str
    id: int
    identifier: str          # «E21», «C9»
    label: str
    prefix: str
    namespace: str
    parents: tuple[int, ...]  # OntoMe-Klassen-Ids; nicht jede liegt im Schnappschuss
    profiles: tuple[int, ...] = ()
    #: Dieselbe URI kann in OntoMe mehrere Ids haben — E21 aus «CIDOC CRM 7.1.3»
    #: und E21 aus «CIDOC CRM 6.2» sind zwei Datensätze mit einer URI. Eine
    #: Property, die auf die 6.2-Id zeigt, meint trotzdem diese Klasse.
    ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class OntoProperty:
    uri: str
    id: int
    identifier: str          # «P67», «P20»
    label: str
    prefix: str
    namespace: str
    domain: int              # OntoMe-Klassen-Id
    range: int
    domain_min: Optional[int] = None
    domain_max: Optional[int] = None   # -1 = unbegrenzt
    range_min: Optional[int] = None
    range_max: Optional[int] = None
    profiles: tuple[int, ...] = ()


@dataclass
class Vocabulary:
    """Alles, was die Schnappschüsse hergeben — nach URI und nach OntoMe-Id."""
    classes: dict[str, OntoClass] = field(default_factory=dict)
    properties: dict[str, OntoProperty] = field(default_factory=dict)
    fetched_at: str = ""
    profile_ids: tuple[int, ...] = ()

    def class_by_id(self, cid: int) -> Optional[OntoClass]:
        return self._by_id.get(cid)

    def __post_init__(self) -> None:
        self._by_id: dict[int, OntoClass] = {}
        self.reindex()

    def reindex(self) -> None:
        self._by_id = {}
        for c in self.classes.values():
            for i in (c.ids or (c.id,)):
                self._by_id[i] = c

    def ancestors(self, uri: str) -> set[str]:
        """Alle Oberklassen-URIs, soweit der Schnappschuss sie kennt.

        Eltern ausserhalb des Schnappschusses werden nicht erraten: eine Klasse,
        deren Eltern fehlen, hat hier eben weniger bekannte Vorfahren — und die
        Prüfung sagt dann «Range nicht belegt», nicht «Range passt».
        """
        out: set[str] = set()
        todo = [uri]
        while todo:
            u = todo.pop()
            c = self.classes.get(u)
            if c is None:
                continue
            for pid in c.parents:
                p = self._by_id.get(pid)
                if p is not None and p.uri not in out:
                    out.add(p.uri)
                    todo.append(p.uri)
        return out


# ── holen ────────────────────────────────────────────────────────────────────

def _get(url: str) -> list:
    import requests
    r = requests.get(url, timeout=120, headers={"Accept": "application/json"})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError(f"unerwartete Antwort von {url}: {type(data).__name__}")
    return data


def fetch_profile(profile_id: int) -> tuple[list, list]:
    classes = _get(f"{API}/classes-profile.json?lang=en&available-in-profile={profile_id}")
    props = _get(f"{API}/properties-profile.json?lang=en&available-in-profile={profile_id}")
    if not classes:
        # Leer ist bei OntoMe kein Fehler, sondern das Wurzelprofil oder eine
        # falsche Id. Beides darf nicht als «Profil hat keine Klassen» im
        # Schnappschuss landen.
        raise ValueError(f"OntoMe-Profil {profile_id} liefert keine Klassen — "
                         f"Wurzelprofil statt publizierter Version, oder falsche Id")
    return classes, props


def refresh(profile_ids: Iterable[int] = tuple(PROFILES),
            out_dir: Path = SNAPSHOT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    before = load(out_dir) if (out_dir / "manifest.json").exists() else None
    manifest = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": API, "profiles": {}}
    for pid in profile_ids:
        classes, props = fetch_profile(pid)
        (out_dir / f"profile-{pid}.classes.json").write_text(
            json.dumps(classes, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        (out_dir / f"profile-{pid}.properties.json").write_text(
            json.dumps(props, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        manifest["profiles"][str(pid)] = {
            "label": classes[0].get("profileLabel", ""),
            "reason": PROFILES.get(pid, ""),
            "classes": len(classes), "properties": len(props)}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    after = load(out_dir)
    diff = {"classes_added": [], "classes_removed": [], "properties_added": [],
            "properties_removed": []}
    if before is not None:
        diff["classes_added"] = sorted(set(after.classes) - set(before.classes))
        diff["classes_removed"] = sorted(set(before.classes) - set(after.classes))
        diff["properties_added"] = sorted(set(after.properties) - set(before.properties))
        diff["properties_removed"] = sorted(set(before.properties) - set(after.properties))
    return {"manifest": manifest, "diff": diff,
            "classes": len(after.classes), "properties": len(after.properties)}


# ── Elternklassen ausserhalb der Profile ─────────────────────────────────────
#
# Ein Profil nennt die Eltern seiner Klassen nur als OntoMe-Ids, und viele davon
# liegen in keinem gewählten Profil (gemessen 14.10.2026: 46 von ihnen, etwa
# E5 Event als Elternteil von sdh-slc:C18). Ohne sie reicht die Hierarchie nicht
# bis CIDOC, und «Range passt» wäre nicht entscheidbar. Es gibt keinen JSON-
# Endpunkt je Klasse; die Klassenseite trägt aber die amtliche URI
# (``<span id="official-uri">``) und die «Subclass of»-Links. Die werden hier
# nachgeschlagen, transitiv, und ebenfalls eingefroren.

_RE_URI = re.compile(r'id="official-uri">\s*([^<\s]+)\s*<')
_RE_IDENT = re.compile(r"<h3>\s*Identifier:\s*([^<]+?)\s*</h3>")
_RE_TITLE = re.compile(r"<title>\s*([^<|]+?)\s*(?:\||</title>)")
_RE_SUBCLASS_BLOCK = re.compile(r"Subclass of:(.*?)(?:Superclass of:|</section>|<h3)", re.S)
_RE_CLASS_LINK = re.compile(r'href="/class/(\d+)/namespace/\d+"')


def _class_page(cid: int) -> dict:
    import requests
    r = requests.get(f"{SITE}/class/{cid}", timeout=120)
    r.raise_for_status()
    html = r.text
    uri = _RE_URI.search(html)
    ident = _RE_IDENT.search(html)
    title = _RE_TITLE.search(html)
    block = _RE_SUBCLASS_BLOCK.search(html)
    parents = sorted({int(x) for x in _RE_CLASS_LINK.findall(block.group(1))} - {cid}) if block else []
    # Keine amtliche URI (gemessen: Klasse 1134) heisst nicht «kaputt», sondern
    # eine Klasse ohne publizierten Namensraum. Sie wird mit ``uri: null``
    # eingefroren, damit die Suche nicht jedes Mal wieder dort hängt — und die
    # Hierarchie endet an ihr, was die Prüfung als «nicht belegt» meldet.
    return {"id": cid, "uri": uri.group(1) if uri else None,
            "identifier": ident.group(1) if ident else "",
            "label": title.group(1).strip() if title else "", "parents": parents}


def resolve_parents(out_dir: Path = SNAPSHOT_DIR, limit: int = 400) -> dict:
    """Alle Eltern-Ids, die kein Profil-Schnappschuss kennt — transitiv, bis die
    Hierarchie geschlossen ist. Schreibt ``classes-by-id.json``."""
    by_id_path = out_dir / "classes-by-id.json"
    cache: dict[str, dict] = (json.loads(by_id_path.read_text(encoding="utf-8"))
                              if by_id_path.exists() else {})
    voc = load(out_dir)
    known = {c.id for c in voc.classes.values()} | {int(k) for k in cache}
    todo = sorted({pid for c in voc.classes.values() for pid in c.parents} - known)
    todo += sorted({pid for e in cache.values() for pid in e["parents"]} - known - set(todo))
    fetched = 0
    while todo and fetched < limit:
        cid = todo.pop(0)
        if cid in known:
            continue
        try:
            entry = _class_page(cid)
        except Exception as exc:  # Netz, 404, Seite ohne Struktur — festhalten, weitermachen
            entry = {"id": cid, "uri": None, "identifier": "", "label": "",
                     "parents": [], "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        cache[str(cid)] = entry
        known.add(cid)
        fetched += 1
        todo += [p for p in entry["parents"] if p not in known and p not in todo]
        # Nach jeder Seite schreiben: ein Abbruch bei Seite 30 soll die 29
        # davor nicht kosten.
        by_id_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                              encoding="utf-8")
    return {"fetched": fetched, "cached": len(cache), "open": len(todo),
            "unresolved": sorted(int(k) for k, v in cache.items() if not v.get("uri"))}


# ── laden ────────────────────────────────────────────────────────────────────

def _qty(v) -> Optional[int]:
    return None if v is None else int(v)


def load(snapshot_dir: Path = SNAPSHOT_DIR) -> Vocabulary:
    """Der Schnappschuss als Vokabular. Dieselbe URI aus mehreren Profilen ist
    **ein** Begriff; die Profile, die ihn führen, stehen daran."""
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    voc = Vocabulary(fetched_at=manifest.get("fetched_at", ""),
                     profile_ids=tuple(int(p) for p in manifest.get("profiles", {})))
    for pid in voc.profile_ids:
        for raw in json.loads((snapshot_dir / f"profile-{pid}.classes.json").read_text(encoding="utf-8")):
            uri = raw["classURI"]
            known = voc.classes.get(uri)
            voc.classes[uri] = OntoClass(
                uri=uri, id=int(raw["classID"]),
                identifier=str(raw.get("classIdentifierInNamespace") or ""),
                label=str(raw.get("classLabel") or ""),
                prefix=str(raw.get("namespacePrefix") or ""),
                namespace=str(raw.get("namespaceURI") or ""),
                parents=tuple(sorted(set(int(x) for x in (raw.get("parentClasses") or []))
                                     | set(known.parents if known else ()))),
                profiles=tuple(sorted(set((known.profiles if known else ()) + (pid,)))),
                ids=tuple(sorted(set((known.ids if known else ()) + (int(raw["classID"]),)))))
        for raw in json.loads((snapshot_dir / f"profile-{pid}.properties.json").read_text(encoding="utf-8")):
            uri = raw["propertyURI"]
            # Dieselbe Property kann in OntoMe mehrmals vorkommen, je Domain —
            # «P8 took place on or within» steht für E67 und für E69 getrennt —
            # und auch je Range: «sdh-slc:P1 was a membership of» steht für C5
            # einmal mit E21 und einmal mit E74 (gemessen; vermutlich E39 Actor
            # in zwei Lesarten). Geschlüsselt nach allen dreien, damit keine
            # Lesart die andere überschreibt; die Prüfung nimmt jede als gültig.
            key = f"{uri}#{raw['propertyDomain']}#{raw['propertyRange']}"
            known = voc.properties.get(key)
            voc.properties[key] = OntoProperty(
                uri=uri, id=int(raw["propertyID"]),
                identifier=str(raw.get("propertyIdentifierInNamespace") or ""),
                label=str(raw.get("propertyLabel") or ""),
                prefix=str(raw.get("namespacePrefix") or ""),
                namespace=str(raw.get("namespaceURI") or ""),
                domain=int(raw["propertyDomain"]), range=int(raw["propertyRange"]),
                domain_min=_qty(raw.get("domainInstancesMinQuantifier")),
                domain_max=_qty(raw.get("domainInstancesMaxQuantifier")),
                range_min=_qty(raw.get("rangeInstancesMinQuantifier")),
                range_max=_qty(raw.get("rangeInstancesMaxQuantifier")),
                profiles=tuple(sorted(set((known.profiles if known else ()) + (pid,)))))
    by_id_path = snapshot_dir / "classes-by-id.json"
    if by_id_path.exists():
        for raw in json.loads(by_id_path.read_text(encoding="utf-8")).values():
            uri = raw.get("uri")
            if not uri:
                continue
            if uri in voc.classes:
                k = voc.classes[uri]
                voc.classes[uri] = OntoClass(**{**k.__dict__, "ids": tuple(sorted(set(k.ids) | {int(raw["id"])})),
                                                "parents": tuple(sorted(set(k.parents) | set(int(x) for x in raw.get("parents") or [])))})
                continue
            voc.classes[uri] = OntoClass(
                uri=uri, id=int(raw["id"]), identifier=str(raw.get("identifier") or ""),
                label=str(raw.get("label") or ""), prefix="",
                namespace=uri.rsplit("/", 1)[0] + "/" if "/" in uri else "",
                parents=tuple(int(x) for x in raw.get("parents") or []), profiles=(),
                ids=(int(raw["id"]),))
    voc.reindex()
    return voc


def property_uris(voc: Vocabulary) -> set[str]:
    return {p.uri for p in voc.properties.values()}


def status(snapshot_dir: Path = SNAPSHOT_DIR) -> str:
    if not (snapshot_dir / "manifest.json").exists():
        return "kein Schnappschuss — zuerst --refresh"
    voc = load(snapshot_dir)
    ns = {}
    for c in voc.classes.values():
        ns.setdefault(c.namespace, [0, 0])[0] += 1
    for p in voc.properties.values():
        ns.setdefault(p.namespace, [0, 0])[1] += 1
    lines = [f"OntoMe-Schnappschuss vom {voc.fetched_at}",
             f"  Profile     {', '.join(str(p) for p in voc.profile_ids)}",
             f"  Klassen     {len(voc.classes)}",
             f"  Properties  {len(property_uris(voc))} ({len(voc.properties)} Domain-Lesarten)",
             "  je Namensraum (Klassen/Properties):"]
    for k, (c, p) in sorted(ns.items()):
        lines.append(f"    {c:4d} / {p:4d}  {k}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="Profile neu holen")
    ap.add_argument("--status", action="store_true", help="was im Schnappschuss liegt")
    ap.add_argument("--resolve", action="store_true",
                    help="nur die Elternklassen ausserhalb der Profile nachschlagen")
    ap.add_argument("--dir", type=Path, default=SNAPSHOT_DIR)
    args = ap.parse_args(argv)
    if args.refresh:
        r = refresh(out_dir=args.dir)
        print(f"geholt: {r['classes']} Klassen, {r['properties']} Property-Lesarten")
        for k, v in r["diff"].items():
            if v:
                print(f"  {k}: {len(v)}")
                for u in v[:20]:
                    print(f"    {u}")
    if args.refresh or args.resolve:
        r = resolve_parents(out_dir=args.dir)
        print(f"Elternklassen nachgeschlagen: {r['fetched']} neu, {r['cached']} im Vorrat, "
              f"{r['open']} offen, ohne amtliche URI: {r['unresolved']}")
    if args.status or not (args.refresh or args.resolve):
        print(status(args.dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""ssrq.py — KG-9 (#653): die Sammlung Schweizerischer Rechtsquellen in den Bestandsgraphen.

    python -m knowledge_hub.sources.ssrq --register ssrq__fuseki_042810.ttl --tei editio-data/data --out build/ssrq.nt --validate

Zwei Quellen, keine davon unsere SQLite:

1. **Das Register** (``ssrq__fuseki_*.ttl``, Dump des SSRQ-Fuseki, Schema
   ``http://ssrq-sds-fds.ch/Register/schemas/persons/``): 27 996 Personen und
   9 134 Gruppen mit Namen (std/orig, Sprache, Fundstelle), Geschlecht,
   Geburt/Tod/Erstnennung (``format_date/when|from|to``), Beruf (Schlüssel,
   Zeitraum, Institution), Wohnort (``residence/location`` → loc-Id ohne
   Ortstabelle), Familie (``org_id``), Verweise (``refs``: GND 803, HLS 1 896,
   dazu Literatur) und 31 Beziehungsarten als Knoten mit ``id`` auf die andere
   Person (spouseOf 1 878, siblingOf 2 569, fatherOf 1 504, …). Gemessen
   14./15.10.2026.
2. **Die TEI der Editionen** (``SSRQ-SDS-FDS/editio-data``, 5 596 Dateien, Stand
   c6f409c): ``<idno>`` (4 854 mit ``SSRQ-``-Kennung), ``<origDate when|from|to>``
   (5 576), und die Verweise im Text — persName 31 613, placeName 30 093,
   orgName 12 518, alle als ``per|loc|org`` + sechs Ziffern, oft mit
   TEI-lokalem Suffix (``per010051a.01``), der auf den Stamm gekürzt wird.

Was **nicht** gebaut wird: ``pers:hist`` (Zeitstempel der Bearbeitung — nie
ein Datum der Person), Literaturverweise als ``owl:sameAs`` (eine
Literaturangabe ist keine Identität; sie wird P3-Notiz), Ortsnamen (der Dump
hat keine; E53 bekommt die Register-URI und kein Label), Editorennamen im
TEI-Kopf (kein ``ref``, keine Erwähnung).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from rdflib import BNode, Graph, Literal, URIRef, RDF, RDFS, XSD

from knowledge_hub import bestandsgraph as bg
from knowledge_hub.sources._common import (
    ExportReport, git_commit, now, validate_graph, write_manifest, write_sorted_ntriples,
)

PERS = "http://ssrq-sds-fds.ch/Register/schemas/persons/"
REG = "http://ssrq-sds-fds.ch/Register/#"
DATASET = bg.Dataset(
    "ssrq", "Sammlung Schweizerischer Rechtsquellen — Register und Editionen",
    "ssrq__fuseki_042810.ttl (28.06.2026) + editio-data c6f409c (26.03.2024)",
    "https://www.ssrq-sds-fds.ch/")
HLS = "https://hls-dhs-dss.ch/de/articles/{id}/"
GND = "https://d-nb.info/gnd/{id}"
EDITION_URL = "https://www.ssrq-sds-fds.ch/persons-db-edit/?query={id}"
LANG = {"deu": "de", "fra": "fr", "ita": "it", "lat": "la", "roh": "rm", "eng": "en"}
SEX = {"1": "männlich", "2": "weiblich"}

#: Beziehungen des Registers → Bausteine. Partnerschaft: C9 mit zwei P20, Art
#: als C10. Eltern: P152, aus Sicht des Kindes. Alles andere: C3 mit Quelle,
#: Ziel und Art. Was hier nicht steht, wird gezählt (``withheld.unknown_relations``).
PARTNERSHIPS = {"spouseOf": "Ehe", "engagedTo": "Verlobung", "divorceeOf": "Scheidung",
                "sexPartnerOf": "Partnerschaft"}
PARENTHOOD = {"fatherOf", "motherOf"}
RELATIONS = {"siblingOf", "halfBrotherOf", "cousinOf", "grandfatherOf", "grandmotherOf", "uncleOf",
             "auntOf", "fatherInLawOf", "motherInLawOf", "brotherInLawOf", "sisterInLawOf",
             "godFatherOf", "guardianOf", "relativeOf", "neighbourOf", "employeeOf", "employerOf",
             "landlordOf", "tenantOf", "ownerOf", "courtierOf", "bailBondsmanOf",
             "judgeAtTheTrialOf", "notaryAtTheTrialOf", "witnessAtTheTrialOf",
             "prosecutionWitnessAtTheTrialOf", "victimAtTheTrialOf", "procuratorInTheTrialAgainst",
             "denouncedDuringTheTrialOf"}
MEMBERSHIPS = {"memberOf": "Mitgliedschaft", "citizenOf": "Bürgerrecht", "nationalOf": "Staatsangehörigkeit"}
_REF = re.compile(r"^(per|loc|org)(\d{6})")
_TEI_REF = re.compile(r'<(persName|placeName|orgName)\b[^>]*\bref="([^"]+)"')
#: SSRQ (deutsch), SDS (Sources du droit suisse, Vaud/Neuchâtel/Fribourg) und FDS
#: (Fonti del diritto svizzero) sind drei Kennungen derselben Sammlung; die
#: erste Fassung kannte nur SSRQ- und liess 740 Vaudoiser Editionen liegen.
#: Ein <idno> kann Attribute tragen (source="…").
_TEI_IDNO = re.compile(r"<idno[^>]*>\s*((?:SSRQ|SDS|FDS)-[^<\s]+)\s*</idno>")
_TEI_ORIGDATE = re.compile(r"<origDate\b([^>]*)>")
_TEI_TITLE = re.compile(r"<titleStmt>.*?<title[^>]*>(.*?)</title>", re.S)
_TEI_REPOSITORY = re.compile(r'<repository[^>]*xml:lang="de"[^>]*>(.*?)</repository>', re.S)
_TEI_SETTLEMENT = re.compile(r'<settlement[^>]*>(.*?)</settlement>', re.S)
_TAGS = re.compile(r"<[^>]+>")


def stem(ref: str) -> Optional[str]:
    m = _REF.match((ref or "").strip())
    return f"{m.group(1)}{m.group(2)}" if m else None


def _date_literal(value: str) -> Optional[tuple[str, str]]:
    """Register- und TEI-Daten: «1342», «1505-02-05», «1493-08»; «1289-1321» nur im Register."""
    v = (value or "").strip()
    if re.fullmatch(r"\d{4}", v):
        return f"{v}-01-01", f"{v}-12-31"
    if re.fullmatch(r"\d{4}-\d{2}", v):
        y, m = int(v[:4]), int(v[5:7])
        import calendar
        if 1 <= m <= 12:
            return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        from datetime import date
        try:
            date.fromisoformat(v)
        except ValueError:
            return None
        return v, v
    m = re.fullmatch(r"(\d{4})-(\d{4})", v)
    if m:
        return f"{m.group(1)}-01-01", f"{m.group(2)}-12-31"
    return None


def _span(b: bg.Build, subject: URIRef, begin: Optional[str], end: Optional[str], note: Optional[str] = None) -> None:
    u = URIRef(str(subject) + "/time-span")
    b.g.add((subject, bg.CRM["P4_has_time-span"], u))
    b.g.add((u, RDF.type, bg.CRM["E52_Time-Span"]))
    if begin:
        b.g.add((u, bg.CRM["P82a_begin_of_the_begin"], Literal(begin, datatype=XSD.date)))
    if end:
        b.g.add((u, bg.CRM["P82b_end_of_the_end"], Literal(end, datatype=XSD.date)))
    if note:
        b.note(u, note)


class _Reg:
    """Lesehilfen über dem Register-Graphen."""

    def __init__(self, g: Graph):
        self.g = g

    def p(self, name: str) -> URIRef:
        return URIRef(PERS + name)

    def one(self, s, name: str):
        return next(self.g.objects(s, self.p(name)), None)

    def many(self, s, name: str):
        return list(self.g.objects(s, self.p(name)))

    def text(self, s, name: str) -> str:
        """Nur Literale und URIs — ein Blank Node ist kein Text. Das Register
        hängt an manche Felder Knoten statt Werte, und ein Blank-Node-Name ist
        bei jedem Einlesen ein anderer: er hätte die Byteidentität gebrochen
        (gefunden an einer «Fundstelle: n22461d98…»)."""
        v = self.one(s, name)
        if v is None or isinstance(v, BNode):
            return ""
        return str(v).strip()

    def when(self, node, literal_field: Optional[str] = None) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """(Anfang, Ende, Rohwert) aus format_dates/format_date: when | from/to |
        notBefore/notAfter — und wo das Register keine formatierte Angabe hat
        (18 378 von 25 970 Erstnennungen, 883 Geburten, 2 491 Sterbedaten;
        gemessen 15.10.2026), aus dem Literal selbst («950», «1289-1321»;
        «um 1235/37» bleibt eine Notiz)."""
        fds = self.one(node, "format_dates")
        fd = self.one(fds, "format_date") if fds is not None else None
        if fd is None:
            raw = self.text(node, literal_field) if literal_field else ""
            if not raw:
                return None, None, None
            d = _date_literal(raw)
            return (d[0], d[1], raw) if d else (None, None, raw)
        w = self.text(fd, "when")
        if w:
            d = _date_literal(w)
            return (d[0], d[1], w) if d else (None, None, w)
        a, z = self.text(fd, "from") or self.text(fd, "notBefore"), self.text(fd, "to") or self.text(fd, "notAfter")
        da, dz = (_date_literal(a) if a else None), (_date_literal(z) if z else None)
        return (da[0] if da else None), (dz[1] if dz else None), (f"{a}–{z}" if (a or z) else None)


def export(register: Path, tei_dir: Optional[Path], out: Path, *, limit: Optional[int] = None,
           validate: bool = False, shacl: bool = False, commit: Optional[str] = None) -> ExportReport:
    t0 = time.monotonic()
    reg = Graph()
    reg.parse(str(register), format="turtle")
    R = _Reg(reg)
    b = bg.Build(DATASET)
    rep = ExportReport(source="ssrq", built_at=now(), db=f"{register} + {tei_dir}", destination=str(out),
                       commit=commit or git_commit())
    counts = Counter({k: 0 for k in (
        "persons", "groups", "names", "gnd", "hls", "literature_notes", "births", "deaths",
        "attestations", "occupations", "residences", "memberships", "partnerships", "parenthood",
        "relations", "documents", "documents_dated", "mentions", "mentions_distinct")})
    held: dict = {"unknown_relations": 0, "relations_to_unknown": 0, "names_without_text": 0,
                  "undated_persons": 0, "families_without_record": 0,
                  "tei_without_idno": 0, "tei_refs_dangling": 0, "tei_refs_unparsed": 0,
                  "unknown_relation_kinds": {}}
    unknown_rel = Counter()

    persons = sorted(reg.subjects(RDF.type, R.p("person")), key=str)
    families = sorted(reg.subjects(RDF.type, R.p("family")), key=str)
    if limit is not None:
        persons = persons[:limit]
    person_set = set(persons)
    family_set = set(families)
    person_uri: dict[str, URIRef] = {}
    family_uri: dict[str, URIRef] = {}

    def key_of(s) -> str:
        return str(s).rsplit("#", 1)[-1]

    def label_of(s, fallback: str) -> str:
        v = next(reg.objects(s, RDFS.label), None)
        return str(v).strip() if v is not None else fallback

    def family_node(s) -> Optional[URIRef]:
        k = key_of(s)
        if k in family_uri:
            return family_uri[k]
        if s not in family_set:
            return None
        u = b.group(k, label_of(s, k), group_type=R.text(s, "org_type") or None, same_as=[str(s)])
        family_uri[k] = u
        counts["groups"] += 1
        _names(s, u, k)
        _refs(s, u)
        return u

    def _names(s, u: URIRef, k: str) -> None:
        for n in R.many(s, "name"):
            text = " ".join(x for x in (R.text(n, "forename"), R.text(n, "surname")) if x)
            if not text:
                held["names_without_text"] += 1
                continue
            kind = R.text(n, "type") or "std"
            lang = LANG.get(R.text(n, "lang"))
            var = key_of(R.one(n, "var_id")) if R.one(n, "var_id") is not None else f"{k}-{text}"
            nu = b.name(u, text, lang=lang, kind=kind, key=var)
            occ = R.text(n, "occurence")
            if occ:
                b.note(nu, f"Fundstelle: {occ}")
            counts["names"] += 1

    def _refs(s, u: URIRef) -> None:
        for r in R.many(s, "refs"):
            t, i, url = R.text(r, "type"), R.text(r, "id"), R.text(r, "url")
            if t == "GND" and i:
                b.same_as(u, [GND.format(id=i)]); counts["gnd"] += 1
            elif t == "HLS" and i:
                b.same_as(u, [HLS.format(id=i.zfill(6))]); counts["hls"] += 1
            elif url:
                b.note(u, f"Literatur ({t or 'ref'}): {url}"); counts["literature_notes"] += 1

    def person_node(s) -> Optional[URIRef]:
        k = key_of(s)
        if k in person_uri:
            return person_uri[k]
        if s not in person_set:
            return None
        u = b.person(k, label_of(s, k), same_as=[str(s)])
        person_uri[k] = u
        counts["persons"] += 1
        _names(s, u, k)
        _refs(s, u)
        sex = R.text(s, "sex")
        if sex in SEX:
            b.g.add((u, bg.CRM["P2_has_type"], b.type_(SEX[sex], "sex")))
        dated = False
        for n in R.many(s, "births"):
            a, z, raw = R.when(n, "birth")
            ev = b.birth(u)
            if a or z:
                _span(b, ev, a, z); dated = True
            elif raw:
                b.note(ev, f"Quelle: {raw}")
            counts["births"] += 1
        for n in R.many(s, "deaths"):
            a, z, raw = R.when(n, "death")
            ev = b.death(u)
            if a or z:
                _span(b, ev, a, z); dated = True
            elif raw:
                b.note(ev, f"Quelle: {raw}")
            counts["deaths"] += 1
        for kind in ("first_mentions", "last_mentions"):
            for n in R.many(s, kind):
                a, z, raw = R.when(n, kind[:-1])          # first_mentions → first_mention
                ev = URIRef(f"{u}/{kind.replace('_', '-')}")
                b.g.add((ev, RDF.type, bg.CRM["E13_Attribute_Assignment"]))
                b.g.add((ev, bg.CRM["P140_assigned_attribute_to"], u))
                b.g.add((ev, bg.CRM["P177_assigned_property_of_type"], b.type_(kind, "attestation")))
                b.g.add((ev, bg.PROV["wasDerivedFrom"], b.dataset_uri))
                occ = R.text(n, "occurence")
                if occ:
                    b.note(ev, f"Fundstelle: {occ}")
                if a or z:
                    _span(b, ev, a, z); dated = True
                elif raw:
                    b.note(ev, f"Quelle: {raw}")
                counts["attestations"] += 1
        if not dated:
            held["undated_persons"] += 1
        for n in R.many(s, "occupation"):
            role = R.text(n, "description") or "occupation"
            a, z, raw = R.when(n, "date")
            emb = b.occupation(u, role, begin=None, end=None)
            if a or z:
                _span(b, emb, a, z)
            org = R.one(n, "org_id")
            if org is not None:
                fam = family_node(org)
                if fam is not None:
                    b.note(emb, f"in {fam}")
            occ = R.text(n, "occurence")
            if occ:
                b.note(emb, f"Fundstelle: {occ}")
            counts["occupations"] += 1
        for n in R.many(s, "residence"):
            loc = R.one(n, "location")
            if loc is None:
                continue
            lk = key_of(loc)
            pl = b.place(lk, None, same_as=[str(loc)])
            b.residence(u, pl)
            counts["residences"] += 1
        for org in R.many(s, "org_id"):
            fam = family_node(org)
            if fam is None:
                held["families_without_record"] += 1
                continue
            b.membership(u, fam, "Familie"); counts["memberships"] += 1
        return u

    for s in persons:
        person_node(s)
    if limit is None:
        for f in families:
            family_node(f)

    # Beziehungen — nach den Knoten, damit beide Seiten existieren.
    for s in persons:
        u = person_uri[key_of(s)]
        for pred in sorted({str(p).replace(PERS, "") for p in reg.predicates(s, None)
                            if str(p).startswith(PERS)}):
            if pred in PARTNERSHIPS or pred in PARENTHOOD or pred in RELATIONS or pred in MEMBERSHIPS:
                for n in R.many(s, pred):
                    target = R.one(n, "id")
                    occ = R.text(n, "occurence")
                    if target is None:
                        held["relations_to_unknown"] += 1
                        continue
                    # Das Register schreibt die Ziel-Id mal als URI, mal als
                    # Literal «http://…/Register/#per000005»; beides ist dieselbe
                    # Person, und ein Literal darf hier keine Lücke erzeugen.
                    target = URIRef(str(target).strip())
                    if pred in MEMBERSHIPS:
                        fam = family_node(target)
                        if fam is None:
                            held["relations_to_unknown"] += 1
                            continue
                        b.membership(u, fam, MEMBERSHIPS[pred]); counts["memberships"] += 1
                        continue
                    tu = person_node(target)
                    if tu is None:
                        held["relations_to_unknown"] += 1
                        continue
                    if pred in PARTNERSHIPS:
                        b.partnership(u, tu, PARTNERSHIPS[pred], note=f"Fundstelle: {occ}" if occ else None)
                        counts["partnerships"] += 1
                    elif pred in PARENTHOOD:
                        b.parent(tu, u); counts["parenthood"] += 1
                    else:
                        b.relation(u, tu, pred, note=f"Fundstelle: {occ}" if occ else None)
                        counts["relations"] += 1
            elif pred.endswith("Of") or pred.endswith("To") or pred.endswith("Against"):
                unknown_rel[pred] += len(R.many(s, pred))

    # Die Editionen
    if tei_dir is not None:
        files = sorted(Path(tei_dir).rglob("*.xml"))
        if limit is not None:
            files = files[:limit]
        for f in files:
            xml = f.read_text(encoding="utf-8", errors="replace")
            m = _TEI_IDNO.search(xml)
            if not m:
                held["tei_without_idno"] += 1
                continue
            doc_id = m.group(1)
            title = _TAGS.sub("", (_TEI_TITLE.search(xml).group(1) if _TEI_TITLE.search(xml) else "")).strip()
            title = re.sub(r"\s+", " ", title)[:300] or doc_id
            du = b.document(doc_id, doc_id, identifier=("ssrq", doc_id), same_as=[EDITION_URL.format(id=doc_id)])
            if title != doc_id:
                b.name(du, title, kind="std", key=f"{doc_id}-title")
            od = _TEI_ORIGDATE.search(xml)
            if od:
                attrs = dict(re.findall(r'(\w+)="([^"]*)"', od.group(1)))
                a = _date_literal(attrs.get("when") or attrs.get("from") or attrs.get("notBefore") or "")
                z = _date_literal(attrs.get("when") or attrs.get("to") or attrs.get("notAfter") or "")
                if a or z:
                    _span(b, b.creation(du), a[0] if a else None, z[1] if z else None)
                    counts["documents_dated"] += 1
            rep_m = _TEI_REPOSITORY.search(xml)
            if rep_m:
                b.note(du, f"Archiv: {_TAGS.sub('', rep_m.group(1)).strip()}")
            counts["documents"] += 1
            seen: set[tuple[str, str]] = set()
            for kind, ref in _TEI_REF.findall(xml):
                st = stem(ref)
                if st is None:
                    held["tei_refs_unparsed"] += 1
                    continue
                target_uri = URIRef(REG + st)
                if st.startswith("per"):
                    tu = person_node(target_uri)
                elif st.startswith("org"):
                    tu = family_node(target_uri)
                else:
                    tu = b.place(st, None, same_as=[str(target_uri)])
                if tu is None:
                    held["tei_refs_dangling"] += 1
                    continue
                b.mention(du, tu)
                counts["mentions"] += 1
                if (doc_id, st) not in seen:
                    seen.add((doc_id, st)); counts["mentions_distinct"] += 1

    held["unknown_relation_kinds"] = dict(unknown_rel.most_common())
    held["unknown_relations"] = sum(unknown_rel.values())
    b.activity("SSRQ-Export (KG-9)", commit=rep.commit or None, started=rep.built_at, ended=now())
    rep.counts = dict(counts)
    rep.withheld = held
    if validate:
        rep.validation = validate_graph(b.g, shacl=shacl)
    rep.triples = write_sorted_ntriples(b.g, out)
    rep.seconds = time.monotonic() - t0
    write_manifest(rep, out)
    return rep


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--register", type=Path, required=True, help="ssrq__fuseki_*.ttl")
    ap.add_argument("--tei", type=Path, default=None, help="editio-data/data (optional)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten n Personen und TEI-Dateien")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--shacl", action="store_true")
    ap.add_argument("--commit", default=None)
    args = ap.parse_args(argv)
    rep = export(args.register, args.tei, args.out, limit=args.limit, validate=args.validate,
                 shacl=args.shacl, commit=args.commit)
    print(rep.render())
    return 0 if (not args.validate or rep.validation.get("ok")) else 1


if __name__ == "__main__":
    sys.exit(main())

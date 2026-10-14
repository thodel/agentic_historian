"""bestandsgraph_fixture.py — ein kleiner Bestandsgraph aus den eingefrorenen Ausschnitten.

Je Bestand **ein** Dokument mit allem, was daran hängt, gebaut mit den Bausteinen
aus ``knowledge_hub.bestandsgraph`` nach der Mapping-Tabelle. Das ist die Vorlage
für KG-7/8/9 — dieselben Aufrufe, nur über alle Zeilen — und der Graph, gegen den
``validate`` und die KG-11-Fragen getestet werden.

Die Ausschnitte unter ``tests/fixtures/bestandsgraph/`` sind am 14.10.2026 aus
den echten Datenbanken gezogen (Seed 20261014) und seither eingefroren.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from rdflib import Graph, URIRef, RDF

from knowledge_hub import bestandsgraph as bg

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bestandsgraph"
PERS = "http://ssrq-sds-fds.ch/Register/schemas/persons/"
REG = "http://ssrq-sds-fds.ch/Register/#"

DODIS = bg.Dataset("dodis", "Dodis — Diplomatische Dokumente der Schweiz, Open Data",
                   "dodis-opendata-2024.sql (Export 2024)", "https://opendata.dodis.ch/")
KF = bg.Dataset("kf", "Die Urkunden und Akten des Klosters und der Hofmeisterei Königsfelden (1300–1658)",
                "kf.db, 30.09.2026")
SSRQ = bg.Dataset("ssrq", "Sammlung Schweizerischer Rechtsquellen — Personen- und Organisationenregister",
                  "ssrq__fuseki_042810.ttl (28.06.2026) + editio-data (26.03.2024)",
                  "https://www.ssrq-sds-fds.ch/")

DODIS_ROLES = {"signatory": "signatory", "adressee": "adressee", "address_of_copy": "address_of_copy"}
HLS = "https://hls-dhs-dss.ch/de/articles/{id}/"
GND = "https://d-nb.info/gnd/{id}"
WD = "https://www.wikidata.org/entity/{id}"


def dodis_graph(doc_index: int = 0) -> Graph:
    d = json.loads((FIXTURES / "dodis.json").read_text(encoding="utf-8"))
    b = bg.Build(DODIS)
    doc = d["documents"][doc_index]
    du = b.document(doc["id"], doc["title"] or f"Dodis {doc['id']}",
                    same_as=[f"https://dodis.ch/{doc['id']}"],
                    begin=doc.get("year"), end=doc.get("year"))
    if doc.get("doc_type"):
        b.g.add((du, bg.CRM["P2_has_type"], b.type_(doc["doc_type"], "document-type")))
    persons = {p["id"]: p for p in d["persons"]}
    places = {p["id"]: p for p in d["places"]}
    tags = {t["id"]: t for t in d["tags"]}
    for link in d["document_persons"]:
        if link["document_id"] != doc["id"]:
            continue
        p = persons[link["person_id"]]
        pu = b.person(p["id"], p["name"] or f"P{p['id']}", same_as=[f"https://dodis.ch/P{p['id']}"])
        b.mention(du, pu)
        if link["relationship_type"] == "author":
            b.author(du, pu)
        elif link["relationship_type"] in DODIS_ROLES:
            b.role(du, pu, DODIS_ROLES[link["relationship_type"]])
    for link in d["document_places"]:
        if link["document_id"] != doc["id"]:
            continue
        pl = places[link["place_id"]]
        same = [WD.format(id=pl["wikidata_id"])] if pl.get("wikidata_id") else []
        plu = b.place(pl["id"], pl["name"], lat=pl.get("latitude"), lon=pl.get("longitude"), same_as=same)
        b.mention(du, plu)
    for link in d["document_tags"]:
        if link["document_id"] != doc["id"]:
            continue
        t = tags[link["tag_id"]]
        b.about(du, b.topic(t["id"], t["name"]))
    return b.g


def kf_graph(entry_index: int = 0) -> Graph:
    d = json.loads((FIXTURES / "kf.json").read_text(encoding="utf-8"))
    b = bg.Build(KF)
    e = d["entries"][entry_index]
    eu = b.document(e["id"], e["short_id"] or e["id"], identifier=("kf", e["id"]),
                    begin=e.get("year"), end=e.get("year"))
    persons = {p["id"]: p for p in d["persons"]}
    places = {p["id"]: p for p in d["places"]}
    orgs = {o["id"]: o for o in d["orgs"]}
    seen: set[str] = set()
    for s in d["spans"]:
        if s["entry_id"] != e["id"] or not s["ref"]:
            continue
        ref = s["ref"]
        if ref in persons:
            p = persons[ref]
            u = b.person(ref, p["main_name"] or p["full_name"] or ref, identifier=("kf", ref))
            if ref not in seen:
                if p.get("forename") or p.get("surname"):
                    b.name(u, " ".join(x for x in (p.get("forename"), p.get("surname")) if x), kind="std", key=f"{ref}-std")
                if p.get("org_ref") and p["org_ref"] in orgs:
                    fam = b.group(p["org_ref"], orgs[p["org_ref"]]["name"] or p["org_ref"],
                                  group_type="Familie", identifier=("kf", p["org_ref"]))
                    b.membership(u, fam, "Familie")
                if p.get("occupation"):
                    b.occupation(u, p["occupation"])
                if p.get("birth"):
                    b.birth(u, when=str(p["birth"]))
                if p.get("death"):
                    b.death(u, when=str(p["death"]))
        elif ref in places:
            pl = places[ref]
            lat = lon = None
            if pl.get("geo"):
                m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", pl["geo"])
                if m:
                    lat, lon = float(m.group(1)), float(m.group(2))
            same = []
            if pl.get("hls_id"):
                same.append(HLS.format(id=str(pl["hls_id"]).zfill(6)))
            if pl.get("gnd_id"):
                same.append(GND.format(id=pl["gnd_id"]))
            u = b.place(ref, pl.get("name_de") or pl.get("name_fr"), lat=lat, lon=lon,
                        same_as=same, identifier=("kf", ref))
        elif ref in orgs:
            o = orgs[ref]
            u = b.group(ref, o["name"] or ref, identifier=("kf", ref))
        else:
            continue   # Ziel ohne Datensatz — zählt KG-8 als dangling_ref
        seen.add(ref)
        b.mention(eu, u)
        if s["text"]:
            b.name(u, s["text"].strip(), kind="orig", key=f"{ref}-{s['span_id'] or s['text']}")
    return b.g


def _strip_ref(ref: str) -> str:
    """``per010051a.01`` → ``per010051``: der Stamm, den das Register kennt."""
    m = re.match(r"^(per|org|loc)(\d{6})", ref)
    return f"{m.group(1)}{m.group(2)}" if m else ref


def ssrq_graph(person_uri: str | None = None) -> Graph:
    reg = Graph().parse(str(FIXTURES / "ssrq_register.ttl"), format="turtle")
    P = lambda n: URIRef(PERS + n)  # noqa: E731
    b = bg.Build(SSRQ)
    persons = sorted(str(s) for s in reg.subjects(RDF.type, P("person")))
    chosen = URIRef(person_uri or persons[0])
    key = str(chosen).rsplit("#", 1)[1]
    label = str(next(reg.objects(chosen, URIRef("http://www.w3.org/2000/01/rdf-schema#label")), key))
    pu = b.person(key, label, same_as=[str(chosen)])
    # Namen
    for n in reg.objects(chosen, P("name")):
        text = " ".join(str(x) for x in (next(reg.objects(n, P("forename")), None),
                                         next(reg.objects(n, P("surname")), None)) if x)
        if not text:
            continue
        kind = str(next(reg.objects(n, P("type")), "std"))
        lang = {"deu": "de", "fra": "fr", "ita": "it"}.get(str(next(reg.objects(n, P("lang")), "")), None)
        var = str(next(reg.objects(n, P("var_id")), "")).rsplit("#", 1)[-1] or f"{key}-{text}"
        nu = b.name(pu, text, lang=lang, kind=kind, key=var)
        occ = next(reg.objects(n, P("occurence")), None)
        if occ is not None:
            b.note(nu, f"Fundstelle: {occ}")
    # Geschlecht, Referenzen
    sex = next(reg.objects(chosen, P("sex")), None)
    if sex is not None:
        b.g.add((pu, bg.CRM["P2_has_type"], b.type_({"1": "männlich", "2": "weiblich"}.get(str(sex), str(sex)), "sex")))
    for r in reg.objects(chosen, P("refs")):
        t = str(next(reg.objects(r, P("type")), ""))
        i = next(reg.objects(r, P("id")), None)
        u = next(reg.objects(r, P("url")), None)
        if t == "GND" and i is not None:
            b.same_as(pu, [GND.format(id=str(i))])
        elif t == "HLS" and i is not None:
            b.same_as(pu, [HLS.format(id=str(i).zfill(6))])
        elif u is not None:
            b.note(pu, f"Literatur ({t}): {u}")
    # Geburt, Tod
    for n in reg.objects(chosen, P("births")):
        when = _when(reg, n, P)
        if when:
            b.birth(pu, when=when)
    for n in reg.objects(chosen, P("deaths")):
        when = _when(reg, n, P)
        if when:
            b.death(pu, when=when)
    # Beruf
    for n in reg.objects(chosen, P("occupation")):
        role = str(next(reg.objects(n, P("description")), "")) or "occupation"
        fd = next(reg.objects(n, P("format_dates")), None)
        begin = end = None
        if fd is not None:
            f = next(reg.objects(fd, P("format_date")), None)
            if f is not None:
                begin = _int(next(reg.objects(f, P("from")), None))
                end = _int(next(reg.objects(f, P("to")), None))
        b.occupation(pu, role, begin=begin, end=end)
    # Wohnort
    for n in reg.objects(chosen, P("residence")):
        loc = next(reg.objects(n, P("location")), None)
        if loc is not None:
            lk = str(loc).rsplit("#", 1)[-1]
            plu = b.place(lk, None, same_as=[str(loc)])
            b.residence(pu, plu)
    # Familie
    for org in reg.objects(chosen, P("org_id")):
        ok = str(org).rsplit("#", 1)[-1]
        olabel = str(next(reg.objects(org, URIRef("http://www.w3.org/2000/01/rdf-schema#label")), ok))
        otype = str(next(reg.objects(org, P("org_type")), "")) or None
        gu = b.group(ok, olabel, group_type=otype, same_as=[str(org)])
        b.membership(pu, gu, "Familie")
    # Verwandtschaft und Beziehungen
    for pred, handler in (("fatherOf", "parent"), ("motherOf", "parent"), ("spouseOf", "partner"),
                          ("siblingOf", "relation"), ("cousinOf", "relation"), ("neighbourOf", "relation")):
        for n in reg.objects(chosen, P(pred)):
            target = next(reg.objects(n, P("id")), None)
            if target is None:
                continue
            tk = str(target).rsplit("#", 1)[-1]
            tlabel = str(next(reg.objects(URIRef(str(target)), URIRef("http://www.w3.org/2000/01/rdf-schema#label")), tk))
            tu = b.person(tk, tlabel, same_as=[str(target)])
            if handler == "parent":
                b.parent(tu, pu)          # chosen ist Vater/Mutter von target
            elif handler == "partner":
                b.partnership(pu, tu, "Ehe")
            else:
                b.relation(pu, tu, pred)
    # Ein Editionsdokument aus den TEI, mit seinen Verweisen
    tei = sorted((FIXTURES / "ssrq_tei").glob("*.xml"))[0]
    xml = tei.read_text(encoding="utf-8")
    idno = re.search(r"<idno>([^<]+)</idno>", xml)
    doc_id = idno.group(1).strip() if idno else tei.stem
    du = b.document(doc_id, doc_id, identifier=("ssrq", doc_id),
                    same_as=[f"https://www.ssrq-sds-fds.ch/persons-db-edit/?query={doc_id}"])
    for m in re.finditer(r'<(persName|placeName|orgName)[^>]*ref="([^"]+)"', xml):
        kind, ref = m.group(1), _strip_ref(m.group(2))
        if kind == "persName":
            eu = b.person(ref, ref, same_as=[REG + ref])
        elif kind == "placeName":
            eu = b.place(ref, None, same_as=[REG + ref])
        else:
            eu = b.group(ref, ref, same_as=[REG + ref])
        b.mention(du, eu)
    return b.g


def _when(reg: Graph, node, P) -> str | None:
    fd = next(reg.objects(node, P("format_dates")), None)
    if fd is None:
        return None
    f = next(reg.objects(fd, P("format_date")), None)
    if f is None:
        return None
    w = next(reg.objects(f, P("when")), None)
    if w is not None:
        return str(w)
    a, z = next(reg.objects(f, P("from")), None), next(reg.objects(f, P("to")), None)
    if a is not None and z is not None:
        return f"{a}-{z}"
    return str(a) if a is not None else None


def _int(v) -> int | None:
    try:
        return int(str(v)) if v is not None else None
    except ValueError:
        return None


def all_graphs() -> Graph:
    g = Graph()
    for part in (dodis_graph(), kf_graph(), ssrq_graph()):
        for t in part:
            g.add(t)
        for pre, ns in part.namespaces():
            g.bind(pre, ns)
    return g

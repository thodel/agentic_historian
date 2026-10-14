# Bestandsgraph — Mapping auf CIDOC CRM 7.1.3 und SDHSS (KG-6, #650)

Stand 14.10.2026. Maschinenlesbar in `knowledge_hub/ontology/registry.py`; gebaut wird
mit `knowledge_hub/bestandsgraph.py`; geprüft mit `knowledge_hub/ontology/validate.py`.

## Woher die Begriffe kommen — und warum OntoMe

Zwei Quellen, beide eingefroren unter `knowledge_hub/ontology/snapshots/`:

| Quelle | Was | Stand |
|---|---|---|
| CIDOC CRM 7.1.3, amtliches RDFS (cidoc-crm.org) | 76 Klassen, 309 Properties mit Domain/Range und Hierarchie | 14.10.2026 |
| OntoMe (ontome.net, LARHRA) — neun publizierte Profile | 105 Klassen, 143 Property-Lesarten aus SDHSS Core, Social Life Core, CRM Supplement, Sources; dazu 56 nachgeschlagene Elternklassen | 14.10.2026 |

OntoMe ist die Pflegeumgebung von SDHSS (Semantic Data for Humanities and Social Sciences),
der Erweiterung, die CIDOC um das ergänzt, was ein Personenregister braucht: Ehe,
Verwandtschaft als Beziehung, Mitgliedschaft mit Zeit, soziale Rolle mit Zeit. Der Grund,
das als **Prüfung** und nicht als Konvention zu führen: der Korpusexporter (`rdf_export.py`)
verwendet 40 Begriffe unter `https://sdhss.org/ontology/` — eine URI, die OntoMe mit
«OntoME URI not found» beantwortet. Die echten Namensräume sind
`https://sdhss.org/ontology/core/` (`sdh:`), `…/social-life-core/` (`sdh-slc:`),
`…/crm-supplement/` (`crm-sup:`). Das wird in KG-12 umgezogen; hier kommt es nicht vor.

Die Profile und ihr Grund stehen in `ontome.PROFILES`. Die API ist undokumentiert; die
Endpunkte stammen aus dem Geovistory-Quellcode und sind im Modul festgehalten.
`python -m knowledge_hub.ontology.ontome --refresh` zieht nach und meldet Unterschiede.

Eine Feststellung aus dem RDFS: **E40 Legal Body gibt es in 7.1.3 nicht** (OntoMe führt
es nur noch unter «CIDOC CRM version 6.2»). Organisationen sind E74 Group mit einem
Gruppentyp (`sdh:P99` → `crm-sup:C9`).

## Die vier Regeln

1. **Erwähnung vom Dokument.** `E31 crm:P67_refers_to Entität`. Es gibt hier keine Lesung
   (das war das Modell des Korpusgraphen, KG-2/3); der Datensatz selbst verweist. E31 ⊂ E73
   ⊂ E89 Propositional Object, der Domain von P67.
2. **Zugewiesen, nicht behauptet.** Eine Rolle aus Dodis (signatory, adressee,
   address_of_copy) ist eine `E13 Attribute Assignment`: P140 Dokument, P141 Person, P177
   Rollentyp (E55). Autorschaft ist `E65 Creation` (P94 Dokument, P14 Person).
3. **Nichts erfunden.** Öffentliche URI → `owl:sameAs`; quellenlokale Id → `E42` mit
   Literal (P190) und Quelle als E55 (P2); Ort ohne Namen → E53 ohne Label; Typ ohne Quelle →
   kein Name.
4. **Jeder Knoten trägt seine Herkunft:** `prov:wasDerivedFrom` → Datensatz (`prov:Entity`
   mit Version); der Bau ist eine `prov:Activity` mit Commit und Zeit.

## Mapping je Quelle

### Dodis (`dodis.db`, Open-Data-Export 2024)

| Feld | Begriff | Bemerkung |
|---|---|---|
| `documents.id, title` | `E31_Document`, `rdfs:label`, `owl:sameAs https://dodis.ch/<id>` | 48 001 |
| `documents.year` / `doc_date` | `P4_has_time-span` → E52 mit P82a/P82b (`xsd:date`) | 47 236 datiert |
| `documents.doc_type` | `P2_has_type` → E55 (Schema `document-type`) | |
| `persons` | `E21_Person`, `owl:sameAs https://dodis.ch/P<id>` | 64 352, ohne GND/Wikidata |
| `document_persons` `mention` | `P67_refers_to` | 251 883 |
| … `author` | `E65_Creation` P94/P14 | 28 272 |
| … `signatory`, `adressee`, `address_of_copy` | `E13` P140/P141/P177 (E55 Rolle) **und** `P67` | 61 398 |
| `places` + `latitude/longitude` | `E53_Place`, `geo:lat/long`, `P168` WKT, `owl:sameAs wd:Q…` | 12 326; 3 217 Wikidata |
| `document_places` | `P67_refers_to` | 372 122 |
| `tags` | `E55_Type` (Schema `topic`) | 1 379 |
| `document_tags` | `P129_is_about` | 129 123 |

### Königsfelden (`kf.db`, Edition 1300–1658)

| Feld | Begriff | Bemerkung |
|---|---|---|
| `entries.id, short_id, title` | `E31_Document`, `rdfs:label` = short_id, `E42` mit `id` (Quelle `kf`) | 1 557; keine öffentliche URI |
| `entries.year` | `P4_has_time-span` | 1 326 datiert |
| `spans` mit `ref` (persName/placeName/orgName) | `P67_refers_to` → E21/E53/E74 | 32 995; 59 Ziele ohne Datensatz → `withheld.dangling_refs` |
| `spans.text` | `E41_Appellation` an der Entität, P190 Text, P2 Typ `orig` | die Schreibung im Dokument |
| `spans` `measure`/`date` | **kein** Tripel | keine Entitäten |
| `persons` | `E21_Person`, `E42` (`kf`); `forename/surname` als `E41` Typ `std`; `birth/death` → E67/E69 mit P4 | 5 260; **0** HLS-Ids |
| `persons.org_ref` | `sdh-slc:C5 Membership` (Typ `Familie`) + `P107` an der E74 | 5 257 |
| `persons.occupation` | `sdh-slc:C13 Social Role Embodiment` → `C12` Rolle | |
| `places` | `E53_Place`; `geo` «lat lon» → geo:lat/long + P168; `hls_id` → `owl:sameAs hls-dhs-dss.ch/de/articles/<id>/`; `gnd_id` → `owl:sameAs d-nb.info/gnd/<id>` | 237 HLS, 170 GND, 384 geo |
| `orgs` | `E74_Group`, `E42` (`kf`) | 2 270 |

### SSRQ (Register-Dump `ssrq__fuseki_042810.ttl` + TEI `editio-data`)

| Feld (Dump, Präfix `pers:`) | Begriff | Bemerkung |
|---|---|---|
| `pers:person` mit `rdfs:label` | `E21_Person`, `owl:sameAs http://ssrq-sds-fds.ch/Register/#per…` | 27 996 |
| `pers:name` (surname/forename, type std/orig, lang, var_id) | `E41_Appellation`, P190 mit Sprachtag, P2 Typ `std`/`orig`; Fundstelle `occurence` als P3 | 81 486 Namen |
| `pers:sex` | `P2_has_type` → E55 (`sex`) | nur so, wie die Quelle es führt |
| `pers:births/deaths` (`format_date/when`) | `E67_Birth`/`E69_Death` + P98/P100, P4 mit when | 1 364 / 3 766 |
| `pers:first_mentions/last_mentions` | P4 am E21? — **nein**: P4 verlangt E2. Als `E13` mit P141 Person, P177 `first_mention`, P4 Zeit | 25 245 / 3 364 |
| `pers:occupation` (description key, date, org_id) | `sdh-slc:C13` → `C12` Rolle (Label = key, später aufgelöst), P4 from/to, P3 Institution | 12 480 |
| `pers:residence` → `location` loc-Id | `P74_has_current_or_former_residence` → `E53` mit `owl:sameAs …/Register/#loc…`, **ohne Label** | 14 555 Personen, 2 322 Orte |
| `pers:org_id` | `C5 Membership` (`Familie`) + `P107` | 27 745 |
| `pers:fatherOf/motherOf` (Knoten mit `id`) | `P152_has_parent`, umgekehrt gelesen | 1 504 / 294 |
| `pers:spouseOf/engagedTo/divorceeOf/sexPartnerOf` | `sdh-slc:C9 Relationship`, zwei P20, P21 Typ (`C10`) | 1 878 / 4 / 7 / 41 |
| `siblingOf, halfBrotherOf, cousinOf, …InLawOf, uncleOf, auntOf, grandfatherOf, relativeOf, neighbourOf, guardianOf, godFatherOf, courtierOf, employeeOf/employerOf, landlordOf/tenantOf, ownerOf, bailBondsmanOf` | `sdh-slc:C3 Social Relationship`, P17 Quelle, P18 Ziel, P16 Typ (`C4`) | 2 569 Geschwister, … |
| `pers:memberOf/citizenOf/nationalOf` | `C5 Membership` mit Typ | 647 / 723 / 23 |
| `pers:refs` type `GND`/`HLS` (`id`) | `owl:sameAs https://d-nb.info/gnd/<id>` / `https://hls-dhs-dss.ch/de/articles/<id>/` | 803 / 1 896 |
| `pers:refs` type `biblio`/`other` (`url`) | `P3_has_note` mit URL | kein sameAs: eine Literaturangabe ist keine Identität |
| `pers:hist` (username, date) | **nichts** | Editor-Zeitstempel, nie ein Datum der Person |
| `pers:family` mit `org_type` | `E74_Group`, `sdh:P99` → `crm-sup:C9` (Familie, Orden, Gericht …) | 9 134 |
| TEI `persName/placeName/orgName @ref` | `P67_refers_to` vom E31 (Edition) | Suffixe `a`, `.01` auf den Stamm gekürzt |
| TEI `<idno>` | `E42` (Quelle `ssrq`), `owl:sameAs` Editions-URL | |

## Was die Prüfung bemerkt

`validate.py` meldet: unbekannte Klasse/Property (nicht im Register oder nicht in
OntoMe/RDFS), Domain- und Range-Verstoss (über Unterklassen, beide Quellen), Objekt ohne
Typ (Range nicht belegt — gemeldet, nicht verschwiegen), fehlende Herkunft, und die SHACL-
Formen aus `shapes.ttl` (zwei Partner je C9, Text je E42, E55 statt Literal an P2,
`owl:sameAs` nur in bekannte Namensräume, `xsd:date` an E52).

Gegenprobe in `tests/test_kg_6_ontology.py`: eine erfundene Property, E40, ein Literal an
P2, eine C9 mit einem Partner und ein Knoten ohne Herkunft lassen die Prüfung scheitern —
jeder mit seinem benannten Befund.

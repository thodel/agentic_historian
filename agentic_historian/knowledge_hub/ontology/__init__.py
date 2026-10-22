"""knowledge_hub.ontology — das Vokabular des Bestandsgraphen, von OntoMe verwaltet (KG-6, #650).

Drei Teile:

- ``ontome``    holt Klassen und Properties der gewählten OntoMe-Profile und friert
                sie als Schnappschuss im Repo ein; offline läuft alles gegen den
                Schnappschuss, ``--refresh`` zieht nach und meldet, was sich änderte.
- ``registry``  das Register der Begriffe, die der Bestandsgraph verwenden darf —
                jeder entweder in OntoMe nachgewiesen oder in unserem eigenen
                Namensraum erklärt. Ein Begriff, der in keinem von beiden steht,
                ist erfunden.
- ``validate``  prüft einen Graphen gegen Register und Schnappschuss: Begriff
                bekannt, Domain und Range passen (mit Unterklassen), Provenienz da.
                Dazu SHACL-Formen für die Regeln, die keine Ontologie ausdrückt.

Warum OntoMe und nicht nur das CIDOC-RDFS: der Bestandsgraph braucht Begriffe,
die CIDOC nicht hat — Ehe, Mitgliedschaft, soziale Beziehung, Wohnort mit Zeit.
SDHSS (Semantic Data for Humanities and Social Sciences) definiert sie, und
OntoMe ist der Ort, an dem SDHSS gepflegt wird. Der Korpusexporter hatte 40
Begriffe unter ``https://sdhss.org/ontology/`` *erfunden* — eine URI, die OntoMe
mit «OntoME URI not found» beantwortet (gemessen 14.10.2026). KG-12 (#659) hat
sie in unseren Namensraum ``shb:`` umgezogen und das Register um sie ergänzt;
seither steht die Prüfung als Tor vor ``corpus_build``. Das soll nicht wieder
vorkommen, und darum ist das Vokabular hier eine Prüfung und keine Konvention.
"""

"""knowledge_hub.sources — die Exporte der Bestände in den Bestandsgraphen (#325, Phase 2).

Ein Modul je Bestand, jedes mit derselben Form:

    python -m knowledge_hub.sources.<bestand> --db <pfad> --out <datei.nt> [--validate]

Jeder Export baut mit ``knowledge_hub.bestandsgraph.Build`` (nur Begriffe aus
dem Register), schreibt sortierte N-Tripel (byteidentisch bei gleicher
Eingabe) und ein Manifest mit den Zählungen, die gegen die SQL-Zählungen der
Quelle zu halten sind. Was die Quelle nicht hergibt, wird gezählt und als
``withheld`` gemeldet, nie erfunden.
"""

# Konsistenz der Hand — die Messung (#773) und die Hypothese (#777)

`agentic_historian/hand_consistency.py` vergleicht die Seiten eines Bandes alle
gegen alle: je Seite x-Höhe, Zeilenabstand, Schrägstellung, Strichbreite,
Tintendichte und das radiale Profil der Buchstabenformen (256 Strahlen, ±5°
Toleranz), nach Ferilli & Piscopo (CEUR Vol-4304, Paper 1). Aus der
Abstandsmatrix kommen drei Dinge: die **Zentralität** jeder Seite (ihre mittlere
Distanz zu allen anderen; die kleinste ist die typischste Seite, #774), die
**Ausreisser** (Zentralität über Mittel + 2σ) und der **Verlauf** (Blockmittel
entlang des Bandes, ein bester Trennpunkt, die Silhouette der besten
Zweiteilung).

## Die Hypothese, und was sie nicht ist

`hypothesis(report)` macht daraus einen Eintrag für die Historikerin — das
Element `Schriftverlauf` in `source_json`, `quelle = berechnet`, `unsicher`
(#772):

> Schriftbild ändert sich ab page-0017 (Trennmass 1.62, Silhouette 0.31);
> page-0009 weicht stark ab. Mögliche neue Hand oder Schreibkampagne. Nicht geprüft.

Ein eigenes Element, keine Zeile in `Weitere_Hinweise`: #772 gibt jedem Element
eine Quelle, und die Hinweise des VLM und eine Rechnung sind zwei. `Schreiber`
setzt die Hypothese nie — ob es eine zweite Hand ist, entscheidet, wer das
Faksimile ansieht. Ausgeliefert wird sie mit `hand_consistency.json` neben
den Outputs (#778 zeigt Matrix und Kurve auf der Ausgabeseite).

Eingeschaltet wird sie mit `HAND_CONSISTENCY_HINT=true`; bis die Tabelle unten
zeigt, dass die Messung Hände trennt, bleibt sie aus.

## Die Schwellen, und woher sie kommen

| Schwelle | Wert | Begründung |
|---|---:|---|
| `HINT_MIN_PAGES` | 8 | Drei Blöcke (`BLOCKS = 3`) brauchen je mindestens zwei Seiten, sonst ist ein Blockmittel eine Seite. |
| `HINT_MAX_PAGES` | 30 | Gleichmässige Stichprobe (`page_sample.spread`) wie bei `validate`; ein Band von 600 Seiten kostet sonst 600 Bildlesungen vor Agent B. |
| `HINT_CHANGE_RATIO` | 1.25 | Mittlere Distanz *über* den Trennpunkt durch die *innerhalb* der Teile. 1.0 ist keine Struktur; die synthetischen Bände der Tests geben > 1.5 bei zwei Händen und ≈ 1.1 bei einer. |
| `HINT_SILHOUETTE` | 0.15 | Silhouette der besten Zweiteilung; ≈ 0 bei einer homogenen Hand. Zusammen mit dem Trennmass, damit ein Band mit einer einzigen fremden Seite keinen Wechsel meldet (die ist ein Ausreisser). |
| `HINT_DRIFT` | 0.10 | Letztes Blockmittel zum ersten, als Anteil — der «Anstieg zum Ende», den Ferilli & Piscopo in ihrem Band lesen. |

Die Werte sind **Anker aus den synthetischen Tests, keine Messung**. Die
Messung ist die Tabelle aus `hand_consistency validate <bench>` über die
e-codices-Bände (#779), deren Katalog die Zahl der Hände nennt: sie zeigt je
Band Silhouette, Trennmass und ob die Hypothese feuert, und darunter, auf wie
vielen Bänden mit mehreren Händen sie feuert und auf wie vielen mit einer. Die
Schwellen sind richtig, wenn die erste Zahl alle und die zweite keine sind;
sonst werden sie hier korrigiert, mit der Tabelle als Beleg.

Was die Tabelle nicht prüft: **wo** die Hand wechselt. Der Katalog nennt die
Zahl der Hände, nicht die Stelle; «auf ±1 Block genau» (#777) braucht ein
Faksimile, bei dem jemand den Wechsel notiert hat.

## Wie es läuft

Nach Agent B, nur für Aufträge mit mehreren Seiten (`_hand_hint` im
Orchestrator): bis zu 30 Seiten des Bandes werden gemessen, der Bericht geht
nach `data/outputs/<doc>_hand.json`, die Hypothese — wenn sie feuert — in
`source_json["Schriftverlauf"]`. Eine Messung, die scheitert, ist kein
gescheiterter Lauf; sie wird geloggt und übersprungen. Der Publisher nimmt die
Datei als `hand_consistency.json` mit.

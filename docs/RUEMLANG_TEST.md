# Rümlang-Test: drei Modelle, ein Druck, ein Schiedsrichter — zurück nach Transkribus

Stand 2026-10-15. Ein Test auf **StAB A V 1446**, dem Protokoll der Berner
Disputation (Januar 1528) in der Hand Eberhard von Rümlangs: Transkribus
collection **2313234**, document **13922129**, 561 Seiten (Transkribus-IDs aus
`thodel/reformation`, `config/disputation_transkribus.json`). Jede Seite, die dort
**keine Zeilen hat oder Zeilen ohne Text**, wird mit drei Gateway-Modellen gelesen,
zeilenweise mit dem Druck von 1528 kollationiert, in strittigen Zeilen von
gpt-oss-120b (GPUStack) entschieden, und geht als **neue `IN_PROGRESS`-Version
derselben Seite** nach Transkribus zurück (die leere Version bleibt als Vorgänger).

Code: `agentic_historian/ruemlang/` (sechs Module), Tests
`tests/test_ah_ruemlang_pipeline.py` (19, offline). Der Lauf selbst findet auf
tei statt: der Gateway hört nur dort (`:8200`), die Secrets liegen in tei's `.env`.

## Was der Repo-Spiegel schon sagt

`thodel/reformation/data/disputation/a_v_1446_ruemlang/pagexml/` ist ein
Transkribus-Sync (`sync_state.json`) aller 561 Seiten. Darin (Stand des Syncs):

| Zustand | Seiten | welche |
|---|---:|---|
| keine Zeilen | **493** | 1, 3, 6–7, 32, 61–99, 104, 108–156, 158–500, 506–561 |
| Zeilen ohne Text | 0 | — |
| teilweise Text | 9 | 9–11, 60, 100–103, 107 |
| voll transkribiert | 59 | 2, 4–5, 8, 12–31, 33–59, 105–106, 157, 501–505 |

Das Live-`fulldoc` entscheidet; der Spiegel sagt nur, womit zu rechnen ist:
**rund 490 Seiten × 3 Modelle.** Mit einem Zeilenmodell bei ~30–40 Zeilen/Seite
und lazy-Reload des Seitenmodells ist das ein Lauf von Stunden, nicht Minuten —
deshalb erst `--limit 3`.

## Die drei Modelle

| Rolle | Modell (Gateway-ID) | Ebene | Liefert |
|---|---|---|---|
| **A** Zeilenmodell | `qwen3vl-medieval-german-v3` | line (kraken segmentiert, Qwen3-VL-4B liest je Crop) | Text **und Geometrie** (bbox, baseline, regions) — die Geometrie des PAGE XML |
| **B** TrOCR | `trocr-kurrent-xvi-xvii` | line (derselbe Segmentierer) | Text je Zeile, Konfidenz |
| **C** Seitenmodell | `qwen3.5-4b-german-kurrent-xvi-xix-page-v1` | page | nur Seitentext; per Zeilen-Alignment auf A's Zeilen gelegt |

Alle drei sind am Gateway registriert und `enabled` (gateway_models, 15.10. 05:47).

**GPU-Lage (15.10., 05:47):** Karte 1 hat 2.8 GiB frei. Resident: Seitenmodell
(13.5 GiB, atr-gateway), party (5.3 GiB), trocr (1.6 GiB) — und **22 GiB ollama
llama-server als root**, nicht unser Dienst. `headroom` meldet für alle drei
`fits: false`. trocr ist schon geladen; das Zeilenmodell braucht ~15.8 GiB, also
muss der Gateway das Seitenmodell entladen. Die Pipeline iteriert modell-major.
Solange ollama 22 GiB hält, lädt der Gateway pro Modellwechsel 12–13 GiB um.

## Ablauf (`ruemlang/pipeline.py`)

```
cd /home/dh/agentic_historian && source .venv/bin/activate
python -m agentic_historian.ruemlang.pipeline plan --dry-run          # col 2313234, doc 13922129 sind Default
python -m agentic_historian.ruemlang.pipeline plan
python -m agentic_historian.ruemlang.pipeline recognize  --limit 3    # Smoke: 3 Seiten × 3 Modelle
python -m agentic_historian.ruemlang.pipeline adjudicate --limit 3    # Druck + Anker aus docs/ruemlang/
python -m agentic_historian.ruemlang.pipeline xml        --limit 3
python -m agentic_historian.ruemlang.pipeline upload     --limit 3 --dry-run
python -m agentic_historian.ruemlang.pipeline upload     --limit 3
# dann ohne --limit; jeder Schritt überspringt, was schon auf der Platte liegt
```

Oder alles in einem: `run --limit 3 --dry-run` plant, liest, adjudiziert und baut
XML, postet aber nichts; `run --limit 3` postet.

1. **plan** — Login (`TRANSKRIBUS_USER` + `TRANSKRIBUS_PASS` oder
   `TRANSKRIBUS_PASSWORD`; legacy `/auth/login`, sonst OIDC-Token von
   account.readcoop.eu, oder fertiges `TRANSKRIBUS_ACCESS_TOKEN`), `fulldoc`
   des Dokuments; ausgewählt wird `nrOfLines == 0` **oder** (`nrOfLines > 0` und
   `nrOfTranscribedLines == 0` und `nrOfWords == 0`) der jüngsten Version
   (`--only-unsegmented` nimmt nur das erste). Schreibt `plan.json`.
2. **recognize** — Seitenbild aus Transkribus (`page.url`); > 3500 px wird für den
   Gateway verkleinert und der Faktor gemerkt; `/recognize` je Modell,
   modell-major; Antwort wörtlich als `rec_<model>.json`.
3. **adjudicate** — B und C per DP-Zeilen-Alignment auf A's Zeilen; die Seite im
   Druck lokalisiert (Wort-Trigramm-Votum im Fenster der Anker ± 2 Druckseiten,
   dann `partial_ratio_alignment`, Mindest-Score 0.55). Findet sie sich nicht,
   bleibt das Ankerfenster als *schwache* Passage: dann bekommt nur eine Zeile
   ≥ 15 Zeichen mit Ähnlichkeit ≥ 0.72 eine Druckstelle. Entscheidung je Zeile:
   identisch → `consensus`; nur u/v, i/j/y, Diakritika, Spatien → `agree_norm`
   (A behält); eine Lesung → `single`; sonst gpt-oss-120b, eine Anfrage je Seite
   mit allen strittigen Zeilen (30 pro Anfrage), JSON-Antwort, geprüft (richtige
   Zeile, Länge 0.5–1.6× der Kandidaten, Ähnlichkeit ≥ 0.5 zu einem Kandidaten),
   sonst `fallback` auf A. Alles mit Begründung in `adjudicated.json`.
4. **xml** — PAGE XML 2019 aus A's Geometrie (Regionen = Hülle ihrer Zeilen;
   Zeile ohne Geometrie wird gemeldet, nicht erfunden), Text = Entscheidung,
   `custom="readingOrder {index:n;} atr {basis:…; conf:…;}"`, Provenienz in
   `<Metadata><Comments>`.
5. **upload** — `POST /collections/2313234/13922129/{pageNr}/text`,
   `status=IN_PROGRESS`, `parent=<tsId der leeren Version>`, `note` mit Modellen
   und Schiedsrichter. `uploaded.json` markiert die Seite; nichts wird zweimal
   gepostet.

## Der Druck von 1528 als Referenz — und wie gut er trifft

`docs/ruemlang/druck1528_transkription.txt` ist die Transkription aus
`thodel/reformation` (`druck_1528/transcriptions/page_N.md`, Transkribus doc
14801624 = e-rara 30973277): 494 Seiten mit Text, 12 967 Zeilen, 717 K Zeichen,
Trennstriche am Zeilenende zusammengezogen, Marker `[Druck 1528 S. n]`.
`docs/ruemlang/print_anchors.json` (ebenfalls aus reformation) gibt je
Handschriftseite das vermutete Druckseitenfenster (23 Seiten per Alignment, der
Rest interpoliert). `druck1528_zwingli_werke_113.txt` (Z VI/1, nur Zwinglis
Voten) liegt daneben, wird aber nicht mehr standardmäßig geladen.

**Gemessen** an den 59 schon transkribierten Rümlang-Seiten (die Transkription als
Stellvertreter für A): wo die Seite im Druck gefunden wird, stimmt die Druckseite
mit dem Anker des Projekts überein — 12→20, 14→22, 15→23, 16→24, 30→27, 31→28,
50→44, 105→87, 501→444, 503→447; dort haben 85–100 % der Zeilen eine Druckstelle.
**Nicht gefunden** werden Seiten 17–28 (außer 20): ihr Text steht so nicht im Druck
(Protokoll ≠ Redaktion; Seite 21: nur 9 von 41 Zeilen ähneln irgendwo im
Ankerfenster dem Druck ≥ 0.70). Dort hilft nur das schwache Fenster, und auch
das nur zeilenweise (Seite 19: 17 von 28 Zeilen). Über 19 Testseiten: 235 von 615
Zeilen bekommen eine Druckstelle. Der Rest wird allein aus A, B, C entschieden —
mit Absicht: ein falsches P ist schlimmer als keines.

Der Prompt sagt dem Schiedsrichter, dass P ein **anderer Textzeuge** ist (Redaktion
aus vier Protokollen), zwischen Varianten entscheiden und Verstümmeltes reparieren
darf, aber nie den Wortlaut der Handschrift ersetzt.

## Entschieden (Antworten vom 15.10.)

- Transkribus-Account: tobiashodel@gmail.com; Passwort in tei's `.env`.
- Kriterium: Seiten ohne Zeilen **und** Seiten mit Zeilen ohne Text.
- Referenz: die Projekt-Transkription des Drucks (oben).
- Schiedsrichter: gpt-oss-120b über GPUStack, Lauf auf tei.
- Upload: neue `IN_PROGRESS`-Version derselben Seite.
- Der ollama-Prozess auf Karte 1 **bleibt**. Folge: Zeilen- und Seitenmodell passen
  nie gleichzeitig; der Gateway lädt beim Modellwechsel um. Die Pipeline fragt
  deshalb modell-major (A über alle Seiten, dann B, dann C): drei Wechsel pro
  Lauf statt drei pro Seite. Jeder Smoke-Lauf mit `--limit` kostet dieselben Wechsel.
- Bildgrenze 3500 px für den Gateway bleibt; Koordinaten werden zurückskaliert.

## Erster Lauf auf tei, 15.10. 09:13 — und woran er hing

`plan` stimmt mit dem Spiegel überein: 561 Seiten, **493 ohne Zeilen**, 0 mit
Zeilen ohne Text (Statusverteilung NEW 492, DONE 10, IN_PROGRESS 59). Login über
`/auth/login`. `run --pages 61-63 --dry-run`:

| Modell | Ergebnis |
|---|---|
| `trocr-kurrent-xvi-xvii` | 38 / 41 / 38 Zeilen, 56–88 s pro Seite — läuft |
| `qwen3vl-medieval-german-v3` | 502 „vLLM process exited (code 1) during startup“ — Journal des Gateways auf idhefix lesen |
| `qwen3.5-4b-german-kurrent-xvi-xix-page-v1` | 503 „GPU 1 has 12538 MB free, needs 15848 MB“ |

Karte 1 danach (`gateway_models`, 09:20): ollama 22 334 MiB (root), party 5 264,
trocr 3 040, kraken 2 310 → **12 538 MiB frei**; beide 4B-Modelle brauchen nach der
Gateway-Formel 15 848 (12 000 × 1.15 + 2 048). Kein vLLM-Modell ist mehr resident.
Solange ollama bleibt, startet auf Karte 1 **keines** der beiden. Karte 0 hat
18 536 MiB frei (RAG-Worker 10 GiB, ollama 16.6 GiB), die Modelle sind aber mit
`gpu_affinity: 1` registriert.

Drei Wege, alle auf dem Serving-Rechner (idhefix), keiner aus der Pipeline heraus:

1. **party pausieren** für den Lauf (`systemctl stop atr-party.service`, 5.3 GiB):
   12.5 + 5.3 = 17.8 GiB ≥ 15.8 → ein 4B-Modell passt, der Gateway lädt beim
   Modellwechsel um. party ist nur die „zweite Meinung“ und wird hier nicht gebraucht.
2. **`gpu_affinity: 0`** für eines oder beide Modelle in `config/models.yaml` von
   serving-atr-inference (Karte 0 hat 18.5 GiB frei), Gateway neu starten. Dann
   läuft ein Modell je Karte, ohne Umladen — der schnellste Lauf, aber Karte 0
   gehört den Nachbarn.
3. ollama verkleinern (kleinerer Kontext als 262 144 oder kleineres Modell) — als
   nicht gewünscht markiert.

Der 502 beim Zeilenmodell ist davon unabhängig zu prüfen: `journalctl -u
atr-gateway.service` auf idhefix, der vLLM-Traceback steht dort. Wahrscheinlich
CUDA-OOM beim Start in die knappe Karte, dann erledigt 1. oder 2. auch das.

Die Pipeline verhält sich bei beidem seit Commit 3 so: ein Modell wird nach drei
aufeinanderfolgenden Fehlern für diesen Lauf aufgegeben (`--give-up`), die
anderen laufen weiter; fehlt das Zeilenmodell, nimmt `adjudicate`/`xml` die
Geometrie aus TrOCR (gleiche kraken-Segmentierung) und sagt das in
`meta.geometry_from` und in `<Comments>`. Ein erneutes `recognize` liest nur
nach, was fehlt — die drei TrOCR-Ergebnisse bleiben.

### Zweiter Lauf, 14:24, party gestoppt (17.8 GiB frei)

Seitenmodell startet (vLLM: 8.82 GiB Gewichte, 1.9 GiB Aktivierung, 4.39 GiB KV
bei utilisation 0.34) und liest drei Seiten in 85 / 7 / 15 s. Das **Zeilenmodell
scheitert weiter**, und das Journal sagt genau woran: für `max_model_len` 16384
braucht es 2.25 GiB KV-Cache, übrig sind 1.78 GiB; „estimated maximum model length
is 12944“. Qwen3-VL-4B hat mehr Gewicht und Vision-Aktivierung als Qwen3.5-4B,
und der Gateway vergibt nur, was nach Reserve frei ist. Kein Modellfehler.

Abhilfe auf idhefix, in `~/Repo/serving-atr-inference/.env`:
`ATR_VLLM_MAX_MODEL_LEN=8192`, dann `systemctl --user restart atr-gateway`.
Gefahrlos, weil Bilder ohnehin auf das Trainingsbudget skaliert werden (Seite
2048 visuelle Tokens, Zeile 262 144 px) und die Ausgabe einer Seite weit unter
dem Rest bleibt; 8192 halbiert nur den KV-Bedarf. Wer 16384 behalten will,
braucht ~3 GiB mehr auf der Karte.

Die Adjudikation des Laufs war wertlos — 117 × `single` — weil das Seitenmodell
gegen die leeren Zeilen des fehlenden Zeilenmodells aligniert wurde; behoben in
dc33505 (Anker = A, sonst B). `adjudicate --force` rechnet neu.

### Dritter Lauf, 15:21 (`ATR_VLLM_MAX_MODEL_LEN=8192`) — und der erste Upload, 17:46

Alle drei Modelle lesen (Zeilenmodell 97 / 15 / 26 s, es lädt beim ersten Aufruf).
Der Schiedsrichter entschied **alle 117 Zeilen** der drei Seiten — kein Konsens,
kein `agree_norm`: die drei Lesungen dieser Hand stimmen nie wörtlich überein.
60–90 s pro Seite für gpt-oss-120b. Entscheidungen verteilen sich über A, B, C;
mit Druckstelle bleibt er bei der Handschrift. Druck lokalisiert für 61 (S. 52–53)
und 63 (S. 53–54), 62 nur schwaches Fenster.

Der Upload scheiterte zuerst mit 500 in 30 ms: Transkribus parst nur den
Namensraum **2013-07-15** und verträgt keinen Doppelpunkt in `custom`-Werten.
Danach: drei Seiten als `IN_PROGRESS`-Versionen hochgeladen, Transkribus zählt
490 statt 493 Seiten ohne Zeilen.

Ob der Schiedsrichter besser wählt als das beste Einzelmodell, misst
`pipeline.py eval --pages 12-14` an transkribierten Seiten (CER je Modell und
fusioniert, eigenes Verzeichnis, kein Upload).

## Der Gesamtlauf

`scripts/ruemlang_full_run.sh start` fährt plan → recognize → adjudicate → xml →
upload unter `nohup`, Log unter `data/vlm_test/ruemlang/logs/`, `status`, `log`,
`stop`; ein erneutes `start` setzt fort. `PAGES=…`, `NO_UPLOAD=1`,
`ADJ_ARGS="--no-llm"` schränken ein. Rechne mit ~95 s pro Seite für die
Erkennung und ~70 s für den Schiedsrichter: rund ein Tag für 490 Seiten, party
bleibt dabei gestoppt.

## Noch offen

1. **Teilweise transkribierte Seiten** (9–11, 60, 100–103, 107): haben Zeilen *mit*
   Text und fallen aus dem Kriterium. Sollen sie trotzdem gelesen werden
   (dann: Geometrie behalten, nur leere Zeilen füllen — eine andere Aufgabe)?
2. **Druckvergleich sichtbar machen**: jetzt nur `basis`/`conf` im `custom` der
   Zeile. Soll die Druckstelle als Kommentar an die Zeile?

## Was von hier aus nicht ging

- `read_pages` (MCP) liefert nur `.txt`, nicht das `.json` mit Zeilengeometrie;
  der Gateway (`130.92.59.240:8200`) ist von außen nicht erreichbar; GPUStack
  antwortet von außen 403; Transkribus-Passwort und API-Keys liegen nur auf tei.
- Der Nextcloud-Share (TU Darmstadt, Laßberg) hat keinen Rümlang-Ordner
  (`share_list` → 404), `start_batch` kann die Seiten also nicht lesen; die
  Pipeline holt die Bilder direkt aus Transkribus.
- Eine eigene OCR des e-rara-Exemplars (kraken, CATMuS Gothic Print, CPU, 73 s pro
  Seite, Stichprobe gut) wurde begonnen und abgebrochen, als die
  Projekt-Transkription des Drucks auftauchte. `ruemlang/ocr_reference.py` bleibt
  als Konverter für den Fall, dass ein Druck ohne Transkription dazukommt.

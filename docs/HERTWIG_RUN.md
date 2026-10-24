# Hertwig (A V 1443): eigenes Kraken-Modell und der Rest des Bandes

Stand 2026-10-22. Transkribus collection 2313234, document **13922499**
(`A_V_1443_Bd_72_Hertwig`), 735 Seiten. Laut Spiegel in `thodel/reformation`:

| Seiten | Zustand | Status |
|---|---|---|
| 1–2 | keine Zeilen | NEW |
| **3–60** | transkribiert: 2 326 Zeilen, 2 273 mit Text, 67 519 Zeichen (13 Seiten mit einzelnen leeren Zeilen) | 49 DONE, 9 IN_PROGRESS |
| 61–735 | keine Zeilen (675 Seiten) | NEW |

Zwei Ziele: (a) ein Kraken-Modell auf den Seiten 3–60 nachtrainieren,
(b) mit diesem Modell als **viertem Leser (D)** und dem Rümlang-Aufbau die
Seiten ohne Zeilen erkennen und als `IN_PROGRESS` hochladen.

## Schritte

| # | Wo | Befehl | Ergebnis |
|---|---|---|---|
| 1 | tei | `gt_export.py … --dry-run`, dann ohne | Baum `VLM_TEST_ROOT/hertwig-gt` mit `A_V_1443_Hertwig_train` / `_test` |
| 2 | MCP | `upload_hf(tree="hertwig-gt", repo_id="dh-unibe/image-text_bern-disputation-1528")` | privates HF-Dataset |
| 3 | tei | `train_kraken.py request`, dann `submit` | Trainingsjob auf asteraix |
| 4 | tei | `train_kraken.py status JOB` | Modell `kraken-bern-hertwig-v1`, nach dem Promotion-Gate am Gateway aktiv |
| 5 | tei | `pipeline.py eval --doc 13922499 --pages 8,17,26,39,48,55 --extra-model kraken-bern-hertwig-v1 …` | CER von A, B, C, D, fusioniert auf Seiten, die das Training nie sah |
| 6 | tei | `RUN=hertwig DOC=13922499 EXTRA_MODEL=… PRIMARY=… bash scripts/ruemlang_full_run.sh start` | Seiten 1–2, 61–735 erkannt, adjudiziert, hochgeladen |

```bash
cd /home/dh/agentic_historian && git fetch origin && git checkout ruemlang-test && git pull
PY=/home/dh/agentic_historian/.venv/bin/python3
$PY agentic_historian/ruemlang/gt_export.py --doc 13922499 --pages 3-60 \
    --test-pages 8,17,26,39,48,55 --tree hertwig-gt --dry-run
$PY agentic_historian/ruemlang/gt_export.py --doc 13922499 --pages 3-60 \
    --test-pages 8,17,26,39,48,55 --tree hertwig-gt
# (2) upload über die ATR-MCP: upload_hf, privat
$PY agentic_historian/ruemlang/train_kraken.py request
$PY agentic_historian/ruemlang/train_kraken.py submit
$PY agentic_historian/ruemlang/train_kraken.py status <JOB_ID>
```

## Entscheidungen

- **Testseiten 8, 17, 26, 39, 48, 55** (vollständig transkribiert, über den
  Bereich verteilt) gehen nicht ins Training. Der Trainer bekommt nur das
  `_train`-Projekt und teilt es selbst 90/10 für die Validierung. Nur so ist
  der Vergleich D gegen A, B, C auf den Testseiten fair.
- **Leere Zeilen** (13 Seiten haben welche) werden im Export entfernt, nicht
  als leere Transkription gelernt.
- **Fine-Tune statt Neutraining**: ~2 000 Zeilen sind zwei Größenordnungen zu
  wenig für ein Modell von Grund auf. Basis `kraken-cursive_inzigkofen`
  (deutsche Kursive 15. Jh., Fine-Tune von CATMuS Medieval), `resize: union`
  für fehlende Zeichen, Batch 16, bis 60 Epochen mit `quit: early`
  (≈ 6 900 Optimizer-Schritte, Untergrenze des Trainers 500).
- **Der vierte Leser** läuft durch dieselbe kraken-Segmentierung wie A und B;
  weicht seine Zeilenzahl ab, wird er inhaltlich zugeordnet. Der Schiedsrichter
  bekommt D mit der Beschreibung „auf dieser Hand nachtrainiert“.
- **Wer gewinnt, wenn nichts entscheidet** (`PRIMARY`), legt Schritt 5 fest.
  Ist D dort klar besser als alles andere, läuft der Gesamtlauf mit
  `PRIMARY=D`, und wenn auch der Schiedsrichter nichts gewinnt, mit
  `ADJ_ARGS="--no-llm"`: dann ist er in Stunden statt in einem Tag fertig.
- **Druckanker** für Hertwig: `docs/ruemlang/print_anchors_a_v_1443.json` aus
  reformation, automatisch gewählt über die Dokument-ID.

## Stand 22.10.: kraken-bern-hertwig-v1

Training: 114 Epochen (early stop, `--epochs` setzt unter `quit: early` nur die
Länge des Cosine-Plans, also lief die Lernrate ab Epoche 60 wieder hoch),
Test-CER des Trainers **0.271** auf seiner Validierung, Promotion-Gate bestanden.

`eval` auf 8, 17, 26, 39, 48, 55, Seiten-CER:

| | A qwen3vl-line | B trocr | C qwen3.5-page | D kraken-v1 | fused (gpt-oss) |
|---|---|---|---|---|---|
| Mittel | 0.446 | 0.424 | 1.521 (S. 48: 7.1, Schleife) | **0.374** | 0.467 |

Der Schiedsrichter macht es schlechter als D allein. Die Seiten-CER vergleicht
aber die ganze Seite in der Zeilenfolge jeder Seite; eine abweichende Folge
kostet wie ein Lesefehler. Darum misst `eval` jetzt zusätzlich die
**Zeilen-CER** (`linecer.py`: jede erkannte Zeile gegen die GT-Zeile, auf der
sie liegt) und die regelbasierte Fusion ohne Schiedsrichter mit A, B oder D als
`primary` (`--variants`, kostet nichts).

## Stand 23.10.: v2, Gesamtlauf

- `kraken-bern-hertwig-v2` (lrate 5e-4, 150 Epochen Plan, Stopp bei 91): Test-CER
  des Trainers **0.233**. Das Gate lief nicht (Cache-Treffer, keine Testseite)
  und wurde auf asteraix von Hand nachgeholt.
- `eval`, Zeilen-CER: D v2 **0.371**, v1 0.382, B 0.442, A 0.460, Fusion mit
  gpt-oss 0.505, nur Regeln mit D 0.383. 41 der 287 GT-Zeilen bekommen keinen
  Text (Segmentierung). Daher gilt der Abstand zu 0.233.
- Gesamtlauf seit 23.10. 19:37 auf tei: D allein, ohne Schiedsrichter, Upload
  als IN_PROGRESS. Plan 642 Seiten.
- Alle Schritte lassen sich über die ATR-MCP starten:
  [`TRANSKRIBUS_PIPELINE_MCP.md`](TRANSKRIBUS_PIPELINE_MCP.md).

# Hertwig (A V 1443): ein eigener Zeilensegmentierer

Stand 2026-10-24. Vorbereitet, noch nicht gelaufen.

## Warum

Der Gesamtlauf (23./24.10., 641 Seiten) las mit `kraken-bern-hertwig-v2`. Auf
den 6 Testseiten lag die Zeilen-CER bei 0,371, auf den Transkribus-Zeilen beim
Trainer bei 0,233. Den Abstand macht die Segmentierung: `blla`, kraken's
eingebautes Standardmodell, gibt **41 der 287 GT-Zeilen (14 %) keinen Text**,
und jede davon zählt voll als Fehler. Ein besserer Leser hilft da nicht; ein
Segmentierer, der diese Hand kennt, schon.

## Die Schritte

| # | Wo | Was |
|---|---|---|
| 1 | tei | Seiten 3–60 exportieren, **mit** leeren Zeilen, XML neben dem Bild |
| 2 | tei | den Baum auf den Trainings-Share kopieren, das Skript daneben |
| 3 | asteraix | `segtrain_kraken.sh`: Fine-Tune von `blla.mlmodel`, dann `segtest` alt gegen neu auf den 6 Testseiten |
| 4 | — | entscheiden nach `segtest.txt` |
| 5 | serving-atr-inference | **fehlt noch:** die Gateway-Route und die kraken-Engine müssen ein Segmentierungsmodell wählen lassen |
| 6 | tei | `pipeline.py eval` mit dem neuen Segmentierer, dann ein neuer Lauf über den Band |

### 1. Export (tei)

```bash
cd /home/dh/agentic_historian && git pull --ff-only
PY=/home/dh/agentic_historian/agentic_historian/.venv/bin/python
$PY agentic_historian/ruemlang/gt_export.py --doc 13922499 --pages 3-60 \
    --test-pages 8,17,26,39,48,55 --tree hertwig-seg --segmentation --dry-run
$PY agentic_historian/ruemlang/gt_export.py --doc 13922499 --pages 3-60 \
    --test-pages 8,17,26,39,48,55 --tree hertwig-seg --segmentation
```

Oder über die ATR-MCP: `transkribus_gt_export(doc="13922499", pages="3-60",
test_pages="8,17,26,39,48,55", tree="hertwig-seg", segmentation=true,
dry_run=false)`.

`--segmentation` behält jede `TextLine`, auch ohne Text: Für einen Segmentierer
ist die Geometrie die Wahrheit, und eine weggelassene Zeile brächte ihm bei,
dass dort keine ist. Beim Erkennungsexport war es umgekehrt, darum ist das ein
eigener Modus. Eine Seite braucht drei Zeilen, nicht drei transkribierte.

### 2. Auf den Share (tei)

Trainer-Jobs liegen unter `/mnt/wbkolleg_dh_1/Textrecognition_Training/` (auf
asteraix), tei schreibt sein Archiv nach `/mnt/wbkolleg_dh_1/tei/`. Erst
nachsehen, ob es derselbe Share ist: `ls /mnt/wbkolleg_dh_1/` auf beiden.

```bash
S=/mnt/wbkolleg_dh_1/Textrecognition_Training/segmentation
mkdir -p $S
cp -r /home/dh/agentic_historian/agentic_historian/data/vlm_test/hertwig-seg $S/
cp /home/dh/agentic_historian/scripts/segtrain_kraken.sh $S/
```

### 3. Training (asteraix)

```bash
S=/mnt/wbkolleg_dh_1/Textrecognition_Training/segmentation
SRC=$S/hertwig-seg nohup bash $S/segtrain_kraken.sh > ~/segtrain-hertwig.log 2>&1 &
tail -f ~/segtrain-hertwig.log
```

Das Skript
- listet die Seiten und bricht ab, wenn einem XML das Bild fehlt;
- nimmt eine **freie** Karte (unter 500 MiB belegt, mindestens 20 GiB frei)
  und bricht sonst ab, statt neben einem Trainer-Job zu laufen. Mit `GPU=1`
  lässt sich eine Karte vorgeben;
- trainiert von kraken's `blla.mlmodel` aus, mit `--resize union` (die
  Regionstypen des Bandes kommen zu denen des Modells dazu), 50 Epochen fest,
  Cosine-Plan auf genau diese 50 Epochen, lrate 2e-4 (kraken's Vorgaben für
  `segtrain`), 10 % der Trainingsseiten für Validierung und Checkpoint-Wahl;
- schreibt das beste Modell als `.mlmodel` (das Format, das die Engine lädt);
- misst danach mit `ketos segtest` auf den 6 Testseiten erst `blla`, dann das
  neue Modell, nebeneinander in `segtest.txt`.

Bei ~45 Trainingsseiten und Batch 1 sind das grob 2 000 Schritte; auf einer
A40 eher eine halbe Stunde als einen Tag. Gemessen ist das nicht.

### 4. Entscheiden

`segtest` misst Pixel-Genauigkeit und IoU sowie Precision, Recall und F1 der
Grundlinien je Klasse. Für uns zählt der **Recall der Grundlinien**: Er sagt,
wie viele der transkribierten Zeilen gefunden werden; die 14 % verlorener
Zeilen sind fehlender Recall. Steigt er deutlich, ohne dass die Precision
fällt, lohnt Schritt 5.

### 5. Was noch fehlt: das Gateway muss den Segmentierer wählen lassen

Heute segmentiert die kraken-Engine (`engines/kraken_svc/app.py` in
serving-atr-inference) **immer** mit `blla.segment(img)`, also mit kraken's
Standardmodell. Für jede Engine, auch TrOCR und die Zeilen-VLMs, kommt die
Segmentierung von dort. Gebraucht wird:

- ein Registry-Eintrag mit `task: segment` für das neue Modell;
- ein Formularfeld `seg_model` an `/segment`, `/recognize` und `/ocr` des
  Gateways, das nur registrierte Segmentierungsmodelle annimmt (kein Pfad von
  aussen);
- in der Engine: Modell einmal laden (`vgsl.TorchVGSLModel.load_model`),
  zwischenspeichern, an `blla.segment(img, model=…)` geben;
- im Segmentierungs-Cache des Gateways das Modell im Schlüssel, sonst kommt
  für dieselbe Seite die alte Segmentierung zurück;
- in `pipeline.py` eine Option `--seg-model`, durchgereicht an `/recognize`.

### 6. Messen und neu lesen

`pipeline.py eval … --extra-model kraken-bern-hertwig-v2 --seg-model …` auf
den 6 Testseiten. Die Zeile «segmentation» zeigt direkt, ob weniger GT-Zeilen
leer bleiben (heute 41/287). Danach ein neuer Lauf über die 641 Seiten; er
legt in Transkribus eine weitere IN_PROGRESS-Version an.

## Warum nicht über die ATR-MCP

Der Export geht über die MCP (`transkribus_gt_export(segmentation=true)`). Das
Training nicht: Der Trainer (`training-atr-models`) kennt nur Erkennungs-
training, und die MCP auf tei startet Training nur über dessen API. Sobald der
Trainer `segtrain` als Aufgabe hat, kann `kraken_train` sie mitnehmen; bis
dahin ist Schritt 3 ein Skript auf asteraix.

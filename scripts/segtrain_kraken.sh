#!/usr/bin/env bash
# segtrain_kraken.sh — fine-tune kraken's line segmenter (blla) on one volume,
# and measure it against the default model on pages training never saw.
#
# Runs on the training box (asteraix), in the trainer's kraken venv, because the
# trainer service trains recognition only (training-atr-models has no segtrain).
#
#   SRC=/mnt/…/segmentation/hertwig-seg bash scripts/segtrain_kraken.sh
#
# SRC is a tree from `gt_export.py --segmentation`: <prefix>_train/ and
# <prefix>_test/, each with p0003.jpg + p0003.xml side by side.
#
# What it does:
#   1. lists the pages (train.lst, test.lst) and refuses if a page lacks its image;
#   2. picks a GPU nobody uses (or GPU=… you name), and refuses rather than share;
#   3. ketos segtrain from kraken's own blla.mlmodel, --resize union (the volume's
#      region types are added to the model's), fixed EPOCHS with the cosine schedule
#      sized to them, 10 % of train for validation and checkpoint choice;
#   4. ketos segtest on the test pages: default blla, then the new model —
#      the same pages, the same metric, side by side in $OUT/segtest.txt.
#
# Knobs: OUT, KRAKEN_VENV, EPOCHS (50), LRATE (2e-4), GPU, MIN_FREE_MIB (20000).
set -euo pipefail

SRC="${SRC:?SRC= the tree written by gt_export.py --segmentation}"
NAME="${NAME:-$(basename "$SRC")}"
OUT="${OUT:-$HOME/atr-cache/segtrain/$NAME-$(date +%Y%m%dT%H%M)}"
KRAKEN_VENV="${KRAKEN_VENV:-$HOME/Repo/training-atr-models/.venvs/kraken-train}"
EPOCHS="${EPOCHS:-50}"
LRATE="${LRATE:-2e-4}"
MIN_FREE_MIB="${MIN_FREE_MIB:-20000}"
KETOS="$KRAKEN_VENV/bin/ketos"
PY="$KRAKEN_VENV/bin/python"

say() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { printf 'segtrain: %s\n' "$*" >&2; exit 1; }

[ -x "$KETOS" ] || die "no ketos at $KETOS (KRAKEN_VENV=…)"
TRAIN_DIR=$(find "$SRC" -maxdepth 1 -type d -name '*_train' | head -1)
TEST_DIR=$(find "$SRC" -maxdepth 1 -type d -name '*_test' | head -1)
[ -n "$TRAIN_DIR" ] || die "no *_train directory in $SRC"
mkdir -p "$OUT"

# 1. manifests — every XML must have its image beside it, or ketos fails later
for split in train test; do
  dir=$([ "$split" = train ] && echo "$TRAIN_DIR" || echo "${TEST_DIR:-}")
  : > "$OUT/$split.lst"
  [ -n "$dir" ] || continue
  for xml in "$dir"/*.xml; do
    [ -f "${xml%.xml}.jpg" ] || die "$xml has no image beside it — was the tree written with --segmentation?"
    echo "$xml" >> "$OUT/$split.lst"
  done
done
say "pages: $(wc -l < "$OUT/train.lst") train, $(wc -l < "$OUT/test.lst") test → $OUT"
[ -s "$OUT/train.lst" ] || die "no training pages"

# 2. a card of our own. The trainer service schedules its jobs on these cards too;
#    a segtrain beside a running fine-tune would take memory it counted on.
if [ -z "${GPU:-}" ]; then
  GPU=$(nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader,nounits \
        | awk -F', ' -v need="$MIN_FREE_MIB" '$2 < 500 && ($3 - $2) >= need {print $1; exit}')
  [ -n "$GPU" ] || die "no idle GPU (used < 500 MiB, ≥ $MIN_FREE_MIB MiB free); name one with GPU=…"
fi
say "GPU $GPU"
export CUDA_VISIBLE_DEVICES="$GPU"

BASE=$("$PY" -c "from importlib import resources; print(resources.files('kraken').joinpath('blla.mlmodel'))")
[ -f "$BASE" ] || die "kraken's default segmentation model not found at $BASE"

# 3. fine-tune. --weights-format coreml: the serving engine loads segmentation
#    models with TorchVGSLModel.load_model, which reads .mlmodel.
say "segtrain from $(basename "$BASE"), $EPOCHS epochs, lrate $LRATE"
"$KETOS" -d cuda:0 segtrain -f page -i "$BASE" --resize union \
    -q fixed -N "$EPOCHS" --cos-max "$EPOCHS" -r "$LRATE" -p 0.9 --augment \
    --weights-format coreml -o "$OUT/checkpoints" -t "$OUT/train.lst" \
    2>&1 | tee "$OUT/segtrain.log"
# kraken names it best_<score>.<format> and its writer may adjust the suffix,
# so take whatever best_* is not a checkpoint.
BEST=$(ls -t "$OUT"/checkpoints/best_* 2>/dev/null | grep -v '\.ckpt$' | head -1 || true)
[ -n "$BEST" ] || die "segtrain wrote no best_* weights — see $OUT/segtrain.log"
cp "$BEST" "$OUT/$NAME.mlmodel"
say "model: $OUT/$NAME.mlmodel (from $(basename "$BEST"))"

# 4. before and after, on pages training never saw
if [ -s "$OUT/test.lst" ]; then
  {
    echo "== default blla"
    "$KETOS" -d cuda:0 segtest -m "$BASE" -f page -e "$OUT/test.lst" 2>&1
    echo "== $NAME"
    "$KETOS" -d cuda:0 segtest -m "$OUT/$NAME.mlmodel" -f page -e "$OUT/test.lst" 2>&1
  } | tee "$OUT/segtest.txt"
fi
say "done: $OUT"

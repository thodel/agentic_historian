#!/usr/bin/env bash
# ruemlang_full_run.sh — the whole Rümlang run on tei, detached with nohup.
#
#   bash scripts/ruemlang_full_run.sh start            # plan → recognize → adjudicate → xml → upload
#   bash scripts/ruemlang_full_run.sh status           # pid, step, counts on disk
#   bash scripts/ruemlang_full_run.sh log              # follow the log
#   bash scripts/ruemlang_full_run.sh stop             # SIGTERM; finished pages stay, `start` resumes
#
# Knobs (environment): PAGES="61-63,100-120" limits the run; NO_UPLOAD=1 stops
# before Transkribus; ADJ_ARGS="--no-llm" is passed to adjudicate; GIVE_UP=5
# is how many consecutive gateway failures retire a model for this run.
#
# Every step skips what is already on disk, so stop/start at any point loses
# at most the page in flight. Expect ~95 s per page for recognition (three
# models, model-major) and ~70 s for the arbiter: roughly a day for 490 pages.
# party must stay stopped on idhefix for the whole run (docs/RUEMLANG_TEST.md).
set -euo pipefail

REPO="${AH_REPO:-/home/dh/agentic_historian}"
PY="$REPO/.venv/bin/python3"
PIPE="$REPO/agentic_historian/ruemlang/pipeline.py"
ROOT="${RUEMLANG_ROOT:-$REPO/agentic_historian/data/vlm_test/ruemlang}"
LOGDIR="$ROOT/logs"
PIDFILE="$LOGDIR/run.pid"
mkdir -p "$LOGDIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
pipe() { "$PY" "$PIPE" "$@"; }
page_args() { [ -n "${PAGES:-}" ] && printf -- '--pages %s' "$PAGES" || true; }

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

count() { find "$ROOT/pages" -maxdepth 2 -name "$1" 2>/dev/null | wc -l; }

cmd_run() {
  echo "$(ts) start: PAGES=${PAGES:-all} NO_UPLOAD=${NO_UPLOAD:-0} ADJ_ARGS=${ADJ_ARGS:-}"
  echo "$(ts) step plan";       pipe plan
  # shellcheck disable=SC2046
  echo "$(ts) step recognize";  pipe recognize $(page_args) --give-up "${GIVE_UP:-5}"
  # shellcheck disable=SC2046,SC2086
  echo "$(ts) step adjudicate"; pipe adjudicate $(page_args) ${ADJ_ARGS:-}
  # shellcheck disable=SC2046
  echo "$(ts) step xml";        pipe xml $(page_args)
  if [ "${NO_UPLOAD:-0}" = "1" ]; then
    echo "$(ts) upload skipped (NO_UPLOAD=1)"
  else
    # shellcheck disable=SC2046
    echo "$(ts) step upload";   pipe upload $(page_args)
  fi
  echo "$(ts) done: $(count 'rec_*.json') recognitions, $(count adjudicated.json) adjudicated, $(count page.xml) xml, $(count uploaded.json) uploaded"
}

case "${1:-}" in
  start)
    if running; then echo "already running, pid $(cat "$PIDFILE") — see: $0 status"; exit 1; fi
    LOG="$LOGDIR/run-$(date +%Y%m%dT%H%M%S).log"
    ln -sfn "$LOG" "$LOGDIR/current.log"
    PAGES="${PAGES:-}" NO_UPLOAD="${NO_UPLOAD:-0}" ADJ_ARGS="${ADJ_ARGS:-}" GIVE_UP="${GIVE_UP:-5}" \
      nohup bash "$0" run > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "started pid $(cat "$PIDFILE"); log: $LOG"
    ;;
  run)   cmd_run ;;
  status)
    if running; then echo "running, pid $(cat "$PIDFILE")"; else echo "not running"; fi
    [ -L "$LOGDIR/current.log" ] && { echo "last step: $(grep -E 'step|done|skipped' "$LOGDIR/current.log" | tail -1)"; }
    echo "on disk: $(count 'rec_qwen3vl*.json') A, $(count 'rec_trocr*.json') B, $(count 'rec_qwen3.5*.json') C, $(count adjudicated.json) adjudicated, $(count page.xml) xml, $(count uploaded.json) uploaded, $(count 'rec_*.error.json') errors"
    ;;
  log)   tail -n 30 -f "$LOGDIR/current.log" ;;
  stop)
    if running; then
      pid=$(cat "$PIDFILE"); pkill -TERM -P "$pid" 2>/dev/null || true; kill -TERM "$pid" 2>/dev/null || true
      echo "stopped pid $pid; finished pages are kept, \`$0 start\` resumes"
    else echo "not running"; fi
    ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac

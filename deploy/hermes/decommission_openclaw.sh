#!/usr/bin/env bash
# decommission_openclaw.sh — H6 (#802) Schritt 6, der Rückbau von OpenClaw nach
# der Beobachtungswoche (Ende 13.11.2026), und die Prüfung zu Schritt 7.
#
#   sudo bash deploy/hermes/decommission_openclaw.sh            # misst, ändert nichts
#   sudo bash deploy/hermes/decommission_openclaw.sh --apply    # die umkehrbaren Schritte
#   sudo bash deploy/hermes/decommission_openclaw.sh --collect-old-values /dev/shm/alte-werte
#   sudo bash deploy/hermes/decommission_openclaw.sh --verify-gone /dev/shm/alte-werte
#
# Ohne Option (Trockenlauf) misst es nur: läuft oder startet OpenClaw noch,
# wo liegt die Binary, welche npm-Pakete sind global, wer braucht Node noch,
# wie gross sind ~/.openclaw und ~/openclaw-workspace, liegt in der Workspace
# ungesicherte Git-Arbeit, gibt es den importierten Skill
# batch-issue-processor noch, und wie stehen die vier Hermes-Cron-Jobs.
#
# --apply tut nur, was sich mit einem `mv` zurückdrehen lässt:
#   1. ~/openclaw-workspace und ~/.openclaw als datierte tar.gz nach ~/archiv/
#      von `dh` (0600 — das Archiv von .openclaw enthält die alten Secrets);
#   2. ~/.openclaw -> ~/.openclaw.pre-migration (was `hermes claw cleanup`
#      tut, hier als `dh`: Hermes läuft als `hermes` und sieht /home/dh nicht);
#      `hermes claw cleanup` selbst nur, wenn auch unter /home/hermes ein
#      OpenClaw-Verzeichnis liegt;
#   3. ~/.hermes/skills/openclaw-imports/batch-issue-processor (der Vorläufer
#      des Queue-Workers — zwei Anleitungen für dieselbe Queue darf es nicht
#      geben) -> ~/archiv/ von `hermes`.
# Es bleibt stehen, bevor es etwas ändert, wenn OpenClaw läuft oder aktiviert
# ist, ein Prozess in der Workspace arbeitet oder die Woche nicht um ist.
# Was sich nicht zurückdrehen lässt — OpenClaw und Node deinstallieren, die
# Workspace löschen, die Discord-Anwendung löschen — druckt es als Befehle für
# den Menschen und führt es nie aus.
#
# --collect-old-values DATEI schreibt die heutigen Werte der Secrets aus
# .env.gpustack und openclaw.json in DATEI (0600, gedacht für /dev/shm), VOR
# der Rotation. --verify-gone DATEI zählt danach, wie viele Zeilen in den
# Live-Dateien noch einen davon enthalten. Beide drucken nie einen Wert, nur
# Namen und Zahlen. Die Checkliste dazu: deploy/hermes/rotation.md.
#
# Exit: 0 gut; 1 --verify-gone fand alte Werte; 2 falscher Aufruf oder kein
# root; 3 --apply blockiert (der Grund steht darüber).
#
# Für Tests und andere Hosts überschreibbar: DECOM_OC_USER (dh),
# DECOM_HERMES_USER (hermes), DECOM_OC_HOME, DECOM_HERMES_HOME,
# DECOM_HERMES_BIN, DECOM_TODAY (JJJJ-MM-TT), DECOM_NOT_BEFORE (2026-11-13),
# DECOM_SYSTEM_ENV (/etc/agentic_historian.env).
set -uo pipefail

MODE=dry
VALUES_FILE=
case "${1:-}" in
  "") ;;
  --apply) MODE=apply ;;
  --collect-old-values) MODE=collect; VALUES_FILE=${2:-} ;;
  --verify-gone) MODE=verify; VALUES_FILE=${2:-} ;;
  -h|--help) sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
  *) echo "unbekannte Option: $1 (--help)" >&2; exit 2 ;;
esac
if [ "$MODE" = collect ] || [ "$MODE" = verify ]; then
  [ -n "$VALUES_FILE" ] || { echo "$1 braucht einen Dateipfad" >&2; exit 2; }
fi

OC_USER=${DECOM_OC_USER:-dh}
H_USER=${DECOM_HERMES_USER:-hermes}
ME=$(id -un)
home_of() { getent passwd "$1" | cut -d: -f6; }
OC_HOME=${DECOM_OC_HOME:-$(home_of "$OC_USER")}
H_HOME=${DECOM_HERMES_HOME:-$(home_of "$H_USER")}
TODAY=${DECOM_TODAY:-$(date +%F)}
NOT_BEFORE=${DECOM_NOT_BEFORE:-2026-11-13}
SYSTEM_ENV=${DECOM_SYSTEM_ENV:-/etc/agentic_historian.env}
UNIT=openclaw-gateway

OC_DIR=$OC_HOME/.openclaw
OC_PRE=$OC_HOME/.openclaw.pre-migration
WS=$OC_HOME/openclaw-workspace
OC_ARCHIVE=$OC_HOME/archiv
ENV_GPUSTACK=$OC_HOME/agentic_historian/.env.gpustack
H_ARCHIVE=$H_HOME/archiv
IMPORTS=$H_HOME/.hermes/skills/openclaw-imports
OLD_SKILL=$IMPORTS/batch-issue-processor
JOB_IDS="0466f6a110f5:dh-bot-queue 8f6c1ac36165:dh-digest ad898cb7126f:repo-status b82a9a2fe5ca:snsf-grants"

if [ "$ME" != root ] && { [ "$ME" != "$OC_USER" ] || [ "$ME" != "$H_USER" ]; }; then
  echo "braucht sudo: liest /home/$OC_USER und /home/$H_USER und handelt als beide." >&2
  exit 2
fi
if [ -z "$OC_HOME" ] || [ -z "$H_HOME" ]; then
  echo "kein Home für $OC_USER oder $H_USER (getent passwd)" >&2
  exit 2
fi
cd / || exit 2     # `hermes` darf das Verzeichnis, aus dem sudo kommt, oft nicht lesen

WARN=0
BLOCK=()
HUMAN=()
say()  { printf '%s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }
ok()   { printf '  [ok] %s\n' "$*"; }
info() { printf '  [..] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*"; WARN=$((WARN + 1)); }
block() { BLOCK+=("$*"); printf '  [STOP] %s\n' "$*"; }
human() { HUMAN+=("$*"); }

# Als Benutzer $1 ausführen: direkt, wenn wir es schon sind, sonst über sudo
# mit dessen Home und Laufzeitverzeichnis (für `systemctl --user`).
as_user() {
  local u=$1; shift
  if [ "$ME" = "$u" ]; then "$@"; return; fi
  sudo -u "$u" -H env XDG_RUNTIME_DIR="/run/user/$(id -u "$u")" "$@"
}
# Wie as_user, aber mit dem PATH der Login-Shell des Benutzers.
as_login() {
  local u=$1; shift
  if [ "$ME" = "$u" ]; then bash -c "$*"; return; fi
  sudo -iu "$u" bash -c "$*"
}
size_of() { if [ -e "$1" ]; then du -sh "$1" 2>/dev/null | cut -f1; else echo "fehlt"; fi; }

# ── die Python-Hälften: lesen Secrets-Dateien, drucken nur Namen ──────────────
# inventory DATEI...  -> die Pfade der Felder, die nach Secret heissen
# collect AUS DATEI.. -> deren Werte nach AUS, Zählung auf stdout
py_secrets() {
  python3 - "$@" <<'PY'
import json, re, sys
from pathlib import Path

NAME = re.compile(r"(token|key|secret|pass|passwd|password|auth|credential|bearer|basic|cookie)", re.I)
ENVNAME = re.compile(r"(TOKEN|KEY|SECRET|PASS|PASSWORD|BASIC|CREDENTIALS?|AUTH)$")

def env_pairs(path):
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        yield key, value

def json_leaves(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from json_leaves(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from json_leaves(v, path + (str(i),))
    elif isinstance(node, (str, int)) and not isinstance(node, bool):
        yield path, str(node)

def label(path):
    return ".".join(p if len(p) <= 40 else "<…>" for p in path)

def plausible(value):
    # a value worth looking for: long enough, not a path, a URL or a sentence
    return len(value) >= 8 and not value.startswith(("/", "~", "http://", "https://")) \
        and not any(c.isspace() for c in value)

def secrets_of(path):
    """(name, value) for each secret-named entry of an env, JSON or token file."""
    p = Path(path)
    if p.suffix == ".json":
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            return [("<kein JSON>", "")]
        return [(label(k), v) for k, v in json_leaves(data) if any(NAME.search(s) for s in k)]
    if p.name.startswith(".env") or p.suffix == ".env":
        return [(k, v) for k, v in env_pairs(p) if ENVNAME.search(k.upper())]
    return [(p.name, p.read_text(encoding="utf-8", errors="replace").strip())]

mode, args = sys.argv[1], sys.argv[2:]
if mode == "inventory":
    for path in args:
        names = [n for n, v in secrets_of(path) if v]
        print(f"     {path}: {len(names)} Feld(er) mit Wert")
        for n in names:
            print(f"       - {n}")
elif mode == "collect":
    out, values = Path(args[0]), []
    for path in args[1:]:
        found = [(n, v) for n, v in secrets_of(path) if plausible(v)]
        values += [v for _, v in found]
        print(f"  {path}: {len(found)} Werte ({', '.join(n for n, _ in found) or '—'})")
    uniq = sorted(set(values))
    with open(out, "x", encoding="utf-8") as fh:      # never overwrite
        fh.write("".join(v + "\n" for v in uniq))
    print(f"  {len(uniq)} verschiedene Werte -> {out}")
PY
}

# ── --collect-old-values / --verify-gone ─────────────────────────────────────
secret_sources() {
  for f in "$ENV_GPUSTACK" "$OC_HOME/dmj_management/.env" "$OC_DIR/openclaw.json" "$OC_PRE/openclaw.json" \
           "$OC_DIR/tmp/.gh_token" "$OC_PRE/tmp/.gh_token"; do
    [ -f "$f" ] && printf '%s\n' "$f"
  done
  for d in "$OC_DIR/credentials" "$OC_PRE/credentials"; do
    [ -d "$d" ] && find "$d" -maxdepth 2 -type f -name '*.json' 2>/dev/null
  done
}

if [ "$MODE" = collect ]; then
  head_ "Alte Werte einsammeln (vor der Rotation) -> $VALUES_FILE"
  if [ -e "$VALUES_FILE" ]; then echo "  $VALUES_FILE gibt es schon — nicht überschrieben." >&2; exit 2; fi
  case "$VALUES_FILE" in /dev/shm/*|/run/*) ;; *) warn "nicht auf tmpfs: die Datei landet auf der Platte; besser /dev/shm/…" ;; esac
  mapfile -t SRC < <(secret_sources)
  [ "${#SRC[@]}" -gt 0 ] || { echo "  keine Quelle gefunden ($ENV_GPUSTACK, $OC_DIR)" >&2; exit 2; }
  ( umask 077; py_secrets collect "$VALUES_FILE" "${SRC[@]}" ) || exit 2
  say "  Die Datei hält Werte, keine Namen; sie wird nie gedruckt. Nach --verify-gone: shred -u $VALUES_FILE"
  exit 0
fi

if [ "$MODE" = verify ]; then
  head_ "Sind die alten Werte weg? (Zählung, keine Werte)"
  [ -r "$VALUES_FILE" ] || { echo "  $VALUES_FILE fehlt oder ist unlesbar" >&2; exit 2; }
  perm=$(stat -c %a "$VALUES_FILE")
  case "$perm" in 600|400) ;; *) warn "$VALUES_FILE hat Modus $perm — chmod 600" ;; esac
  pat=$(mktemp); trap 'rm -f "$pat"' EXIT
  # Leere und kurze Zeilen würden auf alles passen.
  awk 'length($0) >= 8' "$VALUES_FILE" > "$pat"
  n=$(wc -l < "$pat")
  [ "$n" -gt 0 ] || { echo "  keine Werte (>= 8 Zeichen) in $VALUES_FILE" >&2; exit 2; }
  info "$n alte Werte"
  live=0
  say "  Live — muss überall 0 sein:"
  for f in "$ENV_GPUSTACK" "$OC_HOME/agentic_historian/.env" \
           "$OC_HOME/agentic_historian/agentic_historian/.env" "$SYSTEM_ENV" \
           "$H_HOME/.hermes/.env" "$H_HOME/.hermes/config.yaml" "$H_HOME/.config/gh/hosts.yml" \
           "$OC_HOME/dmj_management/.env"; do
    if [ ! -e "$f" ]; then printf '     %-62s fehlt\n' "$f"; continue; fi
    if [ ! -r "$f" ]; then printf '     %-62s unlesbar\n' "$f"; live=$((live + 1)); continue; fi
    c=$(LC_ALL=C grep -c -F -f "$pat" "$f" 2>/dev/null); c=${c:-0}
    printf '     %-62s %s\n' "$f" "$c"
    live=$((live + c))
  done
  say "  Archiv — darf Werte halten, bis es gelöscht ist (die Werte sind dann widerrufen):"
  for d in "$OC_DIR" "$OC_PRE" "$H_HOME/openclaw-src"; do
    [ -e "$d" ] || continue
    c=$(LC_ALL=C grep -rlF -f "$pat" "$d" 2>/dev/null | wc -l)
    printf '     %-62s %s Dateien\n' "$d/" "$c"
  done
  info "tar.gz unter $OC_ARCHIVE sind komprimiert und werden nicht durchsucht."
  if [ "$live" -eq 0 ]; then ok "kein alter Wert mehr in den Live-Dateien"; exit 0; fi
  warn "$live Zeilen mit alten Werten in den Live-Dateien — die Rotation ist nicht fertig"
  exit 1
fi

# ── Trockenlauf und --apply: messen ──────────────────────────────────────────
say "OpenClaw-Rückbau (#802 Schritt 6) — $( [ "$MODE" = apply ] && echo 'APPLY' || echo 'Trockenlauf, ändert nichts' ), $TODAY"
say "  $OC_USER: $OC_HOME   $H_USER: $H_HOME"

if [[ "$TODAY" < "$NOT_BEFORE" ]]; then
  if [ "$MODE" = apply ]; then block "die Beobachtungswoche läuft bis $NOT_BEFORE"; else info "Beobachtungswoche bis $NOT_BEFORE"; fi
fi

head_ "1. Läuft OpenClaw noch?"
OC_UID=$(id -u "$OC_USER" 2>/dev/null || echo 0)
if ! command -v systemctl >/dev/null; then
  warn "systemctl fehlt — nicht messbar"
elif [ "$ME" != "$OC_USER" ] && [ ! -d "/run/user/$OC_UID" ]; then
  warn "kein User-Manager für $OC_USER (/run/user/$OC_UID fehlt) — Unit-Zustand nicht messbar"
else
  active=$(as_user "$OC_USER" systemctl --user is-active "$UNIT" 2>/dev/null)
  enabled=$(as_user "$OC_USER" systemctl --user is-enabled "$UNIT" 2>/dev/null)
  unit_file=$(as_user "$OC_USER" systemctl --user show -p FragmentPath --value "$UNIT" 2>/dev/null)
  active=${active:-unbekannt}; enabled=${enabled:-unbekannt}
  if [ "$active" = active ] || [ "$active" = activating ]; then
    warn "$UNIT ist $active"; block "$UNIT läuft — erst klären, warum (die Rückweg-Probe?)"
  else ok "$UNIT: $active"; fi
  case "$enabled" in
    enabled|enabled-runtime|linked|alias) warn "$UNIT ist $enabled"; block "$UNIT startet beim nächsten Login wieder — systemctl --user disable $UNIT" ;;
    *) ok "$UNIT: $enabled" ;;
  esac
  if [ -n "$unit_file" ]; then
    info "Unit-Datei: $unit_file"
    human "als $OC_USER: systemctl --user disable --now $UNIT; rm $unit_file; systemctl --user daemon-reload"
  fi
fi
# Prozesse: nur PID, Benutzer, Programmname — die Argumente können Tokens tragen.
procs=$(ps -eo pid=,user=,comm=,args= 2>/dev/null \
  | awk -v self=$$ 'tolower($0) ~ /openclaw/ && $0 !~ /decommission_openclaw/ && $1 != self {print $1, $2, $3}')
if [ -n "$procs" ]; then
  warn "Prozesse mit openclaw in der Kommandozeile:"; printf '       %s\n' "$procs"
  block "ein OpenClaw-Prozess läuft"
else ok "kein OpenClaw-Prozess"; fi

head_ "2. OpenClaw-Binary und globale npm-Pakete"
oc_bin=$(as_login "$OC_USER" 'command -v openclaw' 2>/dev/null | tail -1)
if [ -z "$oc_bin" ]; then
  for c in "$OC_HOME/.npm-global/bin/openclaw" "$OC_HOME/.local/bin/openclaw" \
           "$OC_HOME"/.nvm/versions/node/*/bin/openclaw /usr/local/bin/openclaw /usr/bin/openclaw; do
    [ -x "$c" ] && { oc_bin=$c; break; }
  done
fi
npm_bin=
if [ -n "$oc_bin" ]; then
  info "openclaw: $oc_bin -> $(readlink -f "$oc_bin" 2>/dev/null)"
  [ -x "$(dirname "$oc_bin")/npm" ] && npm_bin=$(dirname "$oc_bin")/npm
else
  ok "keine openclaw-Binary im PATH von $OC_USER oder an den üblichen Orten"
fi
[ -n "$npm_bin" ] || npm_bin=$(as_login "$OC_USER" 'command -v npm' 2>/dev/null | tail -1)
if [ -n "$npm_bin" ]; then
  prefix=$(as_user "$OC_USER" "$npm_bin" prefix -g 2>/dev/null)
  pkgs=$(as_user "$OC_USER" "$npm_bin" ls -g --depth=0 --parseable 2>/dev/null \
         | sed -n '2,$p' | sed -E 's#.*/node_modules/##' | sort)
  info "npm: $npm_bin, globaler Prefix $prefix (gehört $(stat -c %U "$prefix" 2>/dev/null || echo ?))"
  info "globale Pakete: $(printf '%s' "$pkgs" | tr '\n' ' ')"
  if printf '%s\n' "$pkgs" | grep -qx openclaw; then
    if [ "$(stat -c %U "$prefix" 2>/dev/null)" = root ]; then
      human "sudo $npm_bin uninstall -g openclaw"
    else
      human "als $OC_USER: $npm_bin uninstall -g openclaw"
    fi
  fi
  others=$(printf '%s\n' "$pkgs" | grep -vxE 'openclaw|npm|corepack' | grep -v '^$')
  [ -z "$others" ] || info "weitere globale Pakete gehen mit Node mit: $(printf '%s' "$others" | tr '\n' ' ')"
else
  info "kein npm für $OC_USER"
fi
[ -z "$oc_bin" ] || [ -n "$npm_bin" ] || human "openclaw ($oc_bin) ohne npm daneben: von Hand entfernen"

head_ "3. Wer braucht Node noch?"
node_keep=()
node_pkg=0
if dpkg-query -W -f='${Status}' nodejs 2>/dev/null | grep -q 'install ok installed'; then
  node_pkg=1
  info "nodejs $(dpkg-query -W -f='${Version}' nodejs 2>/dev/null) aus dem Paket"
  rdeps=$(apt-cache rdepends --installed nodejs 2>/dev/null \
          | awk 'f && NF {gsub(/^[ |]+/, ""); if ($0 != "nodejs") print} /^Reverse Depends:/ {f=1}' | sort -u)
  if [ -n "$rdeps" ]; then
    warn "installierte Pakete hängen an nodejs: $(printf '%s' "$rdeps" | tr '\n' ' ')"
    node_keep+=("apt: $(printf '%s' "$rdeps" | tr '\n' ' ')")
  else ok "kein installiertes Paket hängt an nodejs (apt-cache rdepends --installed)"; fi
  srcs=$(grep -rlsi nodesource /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null \
         | cat - <(ls -d /etc/apt/keyrings/nodesource* /usr/share/keyrings/nodesource* 2>/dev/null) | tr '\n' ' ')
  srcs=${srcs% }
  [ -z "$srcs" ] || info "nodesource-Quelle und Schlüssel: $srcs"
else
  info "kein nodejs-Paket installiert"; srcs=
fi
others_node=$(ps -eo user=,comm=,args= 2>/dev/null \
  | awk 'tolower($0) !~ /openclaw/ && ($2 ~ /^(node|nodejs|npm|npx)$/) {print $1}' | sort | uniq -c | awk '{print $2 " (" $1 ")"}')
if [ -n "$others_node" ]; then
  warn "andere Node-Prozesse laufen: $(printf '%s' "$others_node" | tr '\n' ' ')"
  node_keep+=("Prozesse: $(printf '%s' "$others_node" | tr '\n' ' ')")
else ok "kein anderer Node-Prozess"; fi
units=$(grep -lsE '(^|[ /=])(node|nodejs|npm|npx)( |$)' /etc/systemd/system/*.service \
        /home/*/.config/systemd/user/*.service 2>/dev/null | grep -v "$UNIT" | tr '\n' ' ')
if [ -n "$units" ]; then
  warn "Units, die Node starten: $units"; node_keep+=("Units: $units")
else ok "keine andere systemd-Unit startet Node"; fi
for u in $(getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 {print $1}'); do
  c=$(crontab -l -u "$u" 2>/dev/null | grep -v '^#' | grep -cE '(^|[ /])(node|npm|npx)( |$)')
  [ "${c:-0}" -gt 0 ] && { warn "crontab von $u ruft Node ($c Zeilen)"; node_keep+=("crontab $u"); }
done
if [ "$node_pkg" = 0 ]; then
  :
elif [ "${#node_keep[@]}" -eq 0 ]; then
  ok "Node wird nach OpenClaw von nichts gebraucht (Gemessenes oben)"
  human "nur wenn Abschnitt 3 so bleibt: sudo apt purge nodejs${srcs:+ && sudo rm $srcs} && sudo apt update"
else
  info "Node bleibt: ${node_keep[*]}"
fi

head_ "4. Die OpenClaw-Dateien"
info "$OC_DIR: $(size_of "$OC_DIR")   $WS: $(size_of "$WS")   $OC_PRE: $(size_of "$OC_PRE")"
info "$H_HOME/openclaw-src (die Import-Kopie): $(size_of "$H_HOME/openclaw-src")"
if [ -d "$WS" ]; then
  while IFS= read -r g; do
    repo=$(dirname "$g")
    dirty=$(as_user "$OC_USER" git -C "$repo" status --porcelain 2>/dev/null | wc -l)
    ahead=$(as_user "$OC_USER" git -C "$repo" rev-list --count '@{u}..HEAD' 2>/dev/null || echo "kein Upstream")
    stash=$(as_user "$OC_USER" git -C "$repo" stash list 2>/dev/null | wc -l)
    line="Git ${repo#"$WS"/}: $dirty geänderte Dateien, ungepusht: $ahead, Stashes: $stash"
    if [ "$dirty" -gt 0 ] || [ "$stash" -gt 0 ] || [ "$ahead" != 0 ]; then warn "$line"; else ok "$line"; fi
  done < <(find "$WS" -maxdepth 3 -name .git -type d 2>/dev/null | sort)
  busy=
  for p in /proc/[0-9]*; do
    cwd=$(readlink "$p/cwd" 2>/dev/null) || continue
    case "$cwd" in "$WS"|"$WS"/*) busy="$busy ${p#/proc/}($(cat "$p/comm" 2>/dev/null))" ;; esac
  done
  if [ -n "$busy" ]; then warn "Prozesse arbeiten in der Workspace:$busy"; block "ein Prozess hat sein Arbeitsverzeichnis in $WS"
  else ok "kein Prozess arbeitet in der Workspace"; fi
fi
c=$(as_user "$OC_USER" crontab -l 2>/dev/null | grep -v '^#' | grep -c 'openclaw')
if [ "${c:-0}" -gt 0 ]; then
  warn "crontab von $OC_USER nennt openclaw in $c Zeilen (crontab -l | grep openclaw) — vor dem Löschen umziehen"
else ok "crontab von $OC_USER nennt openclaw nicht"; fi
if [ -d "$OC_DIR" ]; then
  say "  Was OpenClaw hielt (Namen, keine Werte) — für die Rotation, deploy/hermes/rotation.md:"
  mapfile -t SRC < <(secret_sources)
  [ "${#SRC[@]}" -eq 0 ] || py_secrets inventory "${SRC[@]}"
  envs=$(find "$OC_HOME" -maxdepth 3 -type f -name '.env*' ! -path '*/node_modules/*' 2>/dev/null \
         | grep -vxF -e "$ENV_GPUSTACK" -e "$OC_HOME/dmj_management/.env" | sort | tr '\n' ' ')
  [ -z "$envs" ] || info "weitere .env-Dateien, die OpenClaw als $OC_USER lesen konnte: $envs"
  keys=$(find "$OC_DIR" -maxdepth 3 -type f \( -name 'id_*' -o -name '*.pem' -o -name '*.key' \) 2>/dev/null)
  [ -z "$keys" ] || info "Schlüsseldateien in .openclaw: $(printf '%s' "$keys" | tr '\n' ' ')"
fi

head_ "5. Hermes"
h_bin=${DECOM_HERMES_BIN:-}
[ -n "$h_bin" ] || { [ -x "$H_HOME/.local/bin/hermes" ] && h_bin=$H_HOME/.local/bin/hermes; }
[ -n "$h_bin" ] || h_bin=$(as_login "$H_USER" 'command -v hermes' 2>/dev/null | tail -1)
if [ -z "$h_bin" ]; then
  warn "keine hermes-Binary für $H_USER gefunden — Cron nicht messbar"
else
  info "hermes: $h_bin"
  if cron=$(as_user "$H_USER" "$h_bin" cron list 2>&1); then
    printf '%s\n' "$cron" | sed 's/^/       /'
    for pair in $JOB_IDS; do
      id=${pair%%:*}; name=${pair#*:}
      if printf '%s' "$cron" | grep -q -e "$id" -e "$name"; then ok "Job $name ($id) da"; else warn "Job $name ($id) fehlt"; fi
    done
    errs=$(printf '%s\n' "$cron" | grep -ciE 'error|fail')
    [ "${errs:-0}" -eq 0 ] && ok "keine Zeile mit error/failed" || warn "$errs Zeilen mit error/failed — hermes cron status"
  else
    warn "hermes cron list scheiterte"
  fi
fi
out=$H_HOME/.hermes/cron/output
if [ -d "$out" ]; then
  for d in "$out"/*/; do
    [ -d "$d" ] || continue
    last=$(find "$d" -type f -printf '%TY-%Tm-%Td %TH:%TM\n' 2>/dev/null | sort | tail -1)
    info "letzte Ausgabe $(basename "$d"): ${last:-keine}"
  done
fi
if [ -d "$OLD_SKILL" ]; then
  warn "der importierte Skill liegt noch da: $OLD_SKILL"
else ok "kein $OLD_SKILL"; fi
if [ -d "$IMPORTS" ]; then
  rest=$(find "$IMPORTS" -mindepth 1 -maxdepth 1 -type d ! -name batch-issue-processor -printf '%f ' 2>/dev/null)
  [ -z "$rest" ] || info "weitere importierte Skills (bleiben, Entscheid beim Menschen): $rest"
fi
h_left=
for d in "$H_HOME/.openclaw" "$H_HOME/.clawdbot" "$H_HOME/.moldbot"; do [ -e "$d" ] && h_left="$h_left $d"; done
[ -z "$h_left" ] && info "unter $H_HOME kein OpenClaw-Verzeichnis — hermes claw cleanup hat dort nichts zu tun" \
                 || info "für hermes claw cleanup:$h_left"

# ── --apply ──────────────────────────────────────────────────────────────────
ARCH_WS=$OC_ARCHIVE/openclaw-workspace-$TODAY.tar.gz
ARCH_OC=$OC_ARCHIVE/dot-openclaw-$TODAY.tar.gz
SKILL_DEST=$H_ARCHIVE/openclaw-imports-$TODAY/batch-issue-processor
DELETE_ON=$(date -d "$TODAY + 30 days" +%F 2>/dev/null || echo "$TODAY + 30 Tage")

archive() {  # <quelle> <ziel.tar.gz>
  local src=$1 dst=$2
  if [ ! -e "$src" ]; then info "$src fehlt — nichts zu archivieren"; return 0; fi
  if [ -e "$dst" ]; then info "$dst gibt es schon — nicht überschrieben"; return 0; fi
  as_user "$OC_USER" mkdir -p -m 700 "$OC_ARCHIVE" || return 1
  as_user "$OC_USER" sh -c 'umask 077 && tar -czf "$1" -C "$2" "$3"' sh "$dst" "$(dirname "$src")" "$(basename "$src")" \
    || { warn "tar für $src scheiterte"; as_user "$OC_USER" rm -f "$dst"; return 1; }
  local n
  n=$(as_user "$OC_USER" tar -tzf "$dst" 2>/dev/null | wc -l)
  [ "$n" -gt 0 ] || { warn "$dst ist leer oder unlesbar"; return 1; }
  ok "$src -> $dst ($n Einträge, $(size_of "$dst"))"
}

if [ "$MODE" = apply ]; then
  head_ "Apply"
  if [ -e "$OC_PRE" ] && [ -e "$OC_DIR" ]; then block "$OC_PRE und $OC_DIR gibt es beide — von Hand zusammenführen"; fi
  if [ "${#BLOCK[@]}" -gt 0 ]; then
    say "  Nichts geändert. Blockiert durch:"; printf '   - %s\n' "${BLOCK[@]}"
    exit 3
  fi
  archive "$WS" "$ARCH_WS" || exit 3
  archive "$OC_DIR" "$ARCH_OC" || exit 3
  if [ -d "$OC_DIR" ]; then
    as_user "$OC_USER" mv "$OC_DIR" "$OC_PRE" && ok "$OC_DIR -> $OC_PRE"
  fi
  if [ -n "$h_left" ] && [ -n "$h_bin" ]; then
    say "  hermes claw cleanup als $H_USER (fragt womöglich nach):"
    as_user "$H_USER" "$h_bin" claw cleanup || warn "hermes claw cleanup scheiterte"
  fi
  if [ -d "$OLD_SKILL" ]; then
    if [ -e "$SKILL_DEST" ]; then warn "$SKILL_DEST gibt es schon — Skill bleibt liegen"
    else
      as_user "$H_USER" mkdir -p -m 700 "$(dirname "$SKILL_DEST")" \
        && as_user "$H_USER" mv "$OLD_SKILL" "$SKILL_DEST" && ok "$OLD_SKILL -> $SKILL_DEST"
      as_user "$H_USER" rmdir "$IMPORTS" 2>/dev/null && info "$IMPORTS war leer und ist weg"
    fi
  fi
  [ -z "$h_bin" ] || { n=$(as_user "$H_USER" "$h_bin" skills list 2>/dev/null | grep -c batch-issue-processor); \
    [ "${n:-0}" -eq 0 ] && ok "hermes skills list kennt batch-issue-processor nicht mehr" || warn "hermes skills list nennt batch-issue-processor noch"; }
  say ""
  say "  Rückweg (bis die Archive gelöscht sind):"
  say "    als $OC_USER: mv $OC_PRE $OC_DIR && systemctl --user start $UNIT   (solange Unit und Binary noch da sind)"
  say "    als $H_USER: mv $SKILL_DEST $OLD_SKILL"
fi

# ── was nur ein Mensch tut ───────────────────────────────────────────────────
[ ! -d "$WS" ] || human "als $OC_USER, wenn Abschnitt 4 keine ungesicherte Git-Arbeit zeigt und $ARCH_WS existiert: rm -rf $WS"
human "Discord Developer Portal: Anwendung dh-bot (1519706253314756758) löschen — macht ihr Token ungültig"
[ ! -d "$H_HOME/openclaw-src" ] || human "als $H_USER, wenn SOUL und Gedächtnis in Hermes stimmen: rm -rf $H_HOME/openclaw-src (Import-Kopie)"
human "am $DELETE_ON, als $OC_USER: rm $ARCH_WS $ARCH_OC && rm -rf $OC_PRE"
human "danach #802 Schritt 7: deploy/hermes/rotation.md; dann der Rapid7-Rescan für #792/#795 (docs/SECURITY_TEI.md)"

head_ "Für den Menschen — nicht ausgeführt, in dieser Reihenfolge"
i=0; for h in "${HUMAN[@]}"; do i=$((i + 1)); printf '  %d. %s\n' "$i" "$h"; done
say ""
if [ "$MODE" = dry ]; then
  say "Trockenlauf: $WARN Hinweise, ${#BLOCK[@]} Blocker. Nichts geändert. --apply führt die umkehrbaren Schritte aus."
else
  say "Apply fertig: $WARN Hinweise."
fi
exit 0

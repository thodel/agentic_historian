#!/usr/bin/env bash
# check_rapid7.sh — is tei ready for the Rapid7 rescan? (#792, #795, #796, #802)
#
# Reads; changes nothing. Checks every point of #792's definition of done and
# what the rescan will look at, one line each:
#
#     OK    <check>: <detail>
#     WARN  <check>: <detail>     known and accepted, or waiting on someone else
#     FAIL  <check>: <detail>     the rescan would report it again
#
# Exit 0 without FAIL, 1 with. Prints no secret: package names, versions,
# paths, unit names, HTTP codes — never file contents.
#
#     sudo bash deploy/tei/check_rapid7.sh            # complete (root sees /root, docker)
#     bash deploy/tei/check_rapid7.sh                 # without root: says what it skipped
#
# CHECK_ROOT (default /) lets the tests point the file searches at a fake tree.

set -uo pipefail

ROOT="${CHECK_ROOT:-/}"
ROOT="${ROOT%/}"
FAILS=0

say() {                      # say LEVEL CHECK DETAIL
    printf '%-5s %s: %s\n' "$1" "$2" "$3"
    [ "$1" = FAIL ] && FAILS=$((FAILS + 1))
    return 0
}

have() { command -v "$1" >/dev/null 2>&1; }

IS_ROOT=0
[ "$(id -u)" -eq 0 ] && IS_ROOT=1
[ "$IS_ROOT" -eq 1 ] || say WARN "privileges" "not root: /root, docker layers and nginx -t are not checked — run with sudo for the full picture"

# Directories a file search must not enter: kernel views, container layers we
# judge through `docker images` instead, and scratch space.
prune_args=()
for p in /proc /sys /dev /run /tmp /var/tmp /var/lib/docker /snap; do
    prune_args+=(-path "$ROOT$p" -prune -o)
done

# ── 1. Java, Log4j, Spring: gone with the TEI Publisher (#793, #794) ─────────
java_hits=$(find "$ROOT/" -xdev "${prune_args[@]}" \
    \( -name 'log4j-core*.jar' -o -path '*/openjdk/release' -o -name 'spring-*.jar' \) \
    -print 2>/dev/null | head -5)
if [ -n "$java_hits" ]; then
    say FAIL "java" "found: $(echo "$java_hits" | tr '\n' ' ')"
else
    say OK "java" "no log4j-core, openjdk or spring jar outside docker layers"
fi
if [ -d "$ROOT/root/teipublisher_container" ]; then
    say FAIL "java" "/root/teipublisher_container is back"
fi

# ── 2. Docker: only the images a running service needs (#792, 05.11.) ───────
if have docker && [ "$IS_ROOT" -eq 1 ]; then
    if ! images=$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null); then
        say WARN "docker" "docker does not answer — images not checked"
        images=""
    fi
    images=$(echo "$images" | sort -u | grep -v '^$' || true)
    bad=$(echo "$images" | grep -Ei 'teipublisher|temurin|openjdk' || true)
    keep='^(ch-h-bot-(app|api|bridge):mcp|swisslib-mcp:latest|adfreiburg/qlever:latest)$'
    extra=$(echo "$images" | grep -Ev "$keep" | grep -v '^$' || true)
    if [ -z "$images" ]; then
        :
    elif [ -n "$bad" ]; then
        say FAIL "docker" "Java image(s) back: $(echo "$bad" | tr '\n' ' ')"
    elif [ -n "$extra" ]; then
        say WARN "docker" "images beyond the keep list: $(echo "$extra" | tr '\n' ' ')"
    else
        say OK "docker" "only ch-h-bot-*:mcp, swisslib-mcp, qlever"
    fi
else
    say WARN "docker" "not checked (needs root and docker)"
fi

# ── 3. systemd and the services that must answer ────────────────────────────
if have systemctl; then
    if ! failed=$(systemctl --failed --no-legend --plain 2>/dev/null); then
        say WARN "systemd" "systemctl does not answer — no failed-unit check"
        failed=""
        sd_ok=0
    else
        sd_ok=1
    fi
    failed=$(echo "$failed" | awk '{print $1}' | grep -v '^$' || true)
    if [ "$sd_ok" -eq 0 ]; then
        :
    elif [ -n "$failed" ]; then
        say FAIL "systemd" "failed units: $(echo "$failed" | tr '\n' ' ')"
    else
        say OK "systemd" "no failed unit"
    fi
    for unit in nginx docker agentic-historian atr-mcp apertus-web R7ScanAssistant; do
        load=$(systemctl show -p LoadState --value "$unit" 2>/dev/null)
        if [ "$load" != "loaded" ]; then
            say WARN "unit $unit" "not found as a system unit (${load:-unknown}) — name changed, or a user unit?"
            continue
        fi
        state=$(systemctl is-active "$unit" 2>/dev/null)
        if [ "$state" = active ]; then say OK "unit $unit" "active"
        else say FAIL "unit $unit" "$state"; fi
    done
else
    say WARN "systemd" "systemctl not available"
fi

if [ "$IS_ROOT" -eq 1 ] && have nginx; then
    if nginx -t >/dev/null 2>&1; then say OK "nginx" "configuration valid"
    else say FAIL "nginx" "nginx -t fails"; fi
fi

# Local ports from the inventory (#792, 02.–05.11.): atr-mcp, the federation's
# SSH tunnels to idhefix, QLever. Any HTTP answer counts; 000 is no answer.
if have curl; then
    for pair in atr-mcp:8300 eos:8000 kf:8001 ssrq:8002 hbls:8003 hls:8004 qlever:7019; do
        name=${pair%%:*}; port=${pair##*:}
        code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$port/" 2>/dev/null)
        if [ -n "$code" ] && [ "$code" != 000 ]; then say OK "port $port" "$name answers ($code)"
        else say FAIL "port $port" "$name does not answer"; fi
    done
fi

# ── 4. Ubuntu Pro and the ten packages (#795) ───────────────────────────────
if have pro; then
    pstatus=$(pro status 2>/dev/null)
    for svc in esm-apps esm-infra; do
        if echo "$pstatus" | grep -Eq "^$svc[[:space:]]+yes[[:space:]]+enabled"; then
            say OK "pro $svc" "enabled"
        else
            say FAIL "pro $svc" "not enabled"
        fi
    done
else
    say FAIL "pro" "the pro client is missing"
fi

installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'; }

# Removed or never installed (02./03.11.); back means the scan finds them again.
back=0
for p in libcjson1 libzvbi0t64 libass9 libmbedcrypto7t64 python3-pip python3-wheel rclone; do
    if installed "$p"; then
        say FAIL "package $p" "installed again — it was removed (#795)"
        back=1
    fi
done
[ "$back" -eq 0 ] && say OK "packages" "none of the seven removed packages is back"

# gh: the queue worker needs it (#799) — from GitHub's own repository, not Ubuntu's.
if installed gh; then
    if apt-cache policy gh 2>/dev/null | grep -A3 '^\s*\*\*\*' | grep -q 'cli.github.com'; then
        say OK "package gh" "from cli.github.com"
    else
        say FAIL "package gh" "installed from the Ubuntu archive — the version Rapid7 reports"
    fi
fi

# The two that stay, fixed by esm-apps once Canonical ships it.
for p in python3-urllib3 python3-pip-whl; do
    pol=$(apt-cache policy "$p" 2>/dev/null)
    inst=$(echo "$pol" | awk '/Installed:/ {print $2}')
    cand=$(echo "$pol" | awk '/Candidate:/ {print $2}')
    if [ -z "$inst" ] || [ "$inst" = "(none)" ]; then
        say WARN "package $p" "not installed"
    elif [ "$inst" != "$cand" ]; then
        say FAIL "package $p" "$inst installed, $cand available — sudo apt upgrade $p"
    elif [[ "$inst" == *esm* ]]; then
        say OK "package $p" "$inst (esm)"
    else
        say WARN "package $p" "$inst, no esm fix published yet — accepted until Canonical ships one (#795)"
    fi
done

upgradable=$(apt list --upgradable 2>/dev/null | grep -c '/' || true)
if [ "${upgradable:-0}" -gt 0 ]; then
    say WARN "apt" "$upgradable package(s) upgradable — sudo apt upgrade before the scan"
else
    say OK "apt" "nothing upgradable"
fi

# ── 5. World-writable files and home directories (#796) ─────────────────────
ww=$(find "$ROOT/" -xdev "${prune_args[@]}" -type f -perm -0002 -print 2>/dev/null)
n_ww=$(echo -n "$ww" | grep -c '^' || true)
if [ "${n_ww:-0}" -gt 0 ]; then
    say WARN "world-writable" "$n_ww file(s), e.g. $(echo "$ww" | head -3 | tr '\n' ' ')"
else
    say OK "world-writable" "no world-writable file"
fi

# root, every account from UID 1000 up, and every account whose home is under
# /home — service accounts with nologin and system UIDs too: the likely #796
# finding was the system account `agentic_historian`, whose home did not exist
# (a scanner reads a missing home as an unsafe mode). nobody (65534) and the
# other system accounts (/nonexistent, /var/lib/…) are meant to have none.
while IFS=: read -r user _ uid _ _ home _; do
    case "$home" in
        /home/*) ;;
        *) [ "$uid" -eq 0 ] 2>/dev/null \
               || { [ "$uid" -ge 1000 ] 2>/dev/null && [ "$uid" -ne 65534 ]; } || continue ;;
    esac
    if [ ! -d "$ROOT$home" ]; then
        say FAIL "home $user" "$home does not exist — create it 0750, owned by $user (#796)"
        continue
    fi
    mode=$(stat -c '%a' "$ROOT$home" 2>/dev/null) || continue
    # anything for "other", or write for the group
    if (( (8#$mode & 8#007) != 0 || (8#$mode & 8#020) != 0 )); then
        say FAIL "home $user" "$home is $mode — chmod 750 (or 700)"
    else
        say OK "home $user" "$home is $mode"
    fi
done < <(getent passwd 2>/dev/null)

# ── 6. What the scan will also see ──────────────────────────────────────────
if [ -f "$ROOT/var/run/reboot-required" ] || [ -f "$ROOT/run/reboot-required" ]; then
    say WARN "reboot" "required (restarts deferred after the package update) — reboot before the scan"
else
    say OK "reboot" "none pending"
fi

oc=$(command -v openclaw 2>/dev/null || true)
nd=$(command -v node 2>/dev/null || true)
ns=$(ls "$ROOT"/etc/apt/sources.list.d/ 2>/dev/null | grep -i nodesource || true)
if [ -n "$oc$nd$ns" ]; then
    names=()
    [ -n "$oc" ] && names+=(openclaw)
    [ -n "$nd" ] && names+=(node)
    [ -n "$ns" ] && names+=(nodesource-repo)
    found="${names[*]}"
    say WARN "openclaw/node" "still on the host ($found) — H6 Rückbau (#802) before the scan saves a second one"
else
    say OK "openclaw/node" "gone"
fi

echo
if [ "$FAILS" -gt 0 ]; then
    echo "$FAILS FAIL — not ready for the rescan."
    exit 1
fi
echo "No FAIL — ready for the rescan (WARNs are known; see docs/SECURITY_TEI.md)."
exit 0

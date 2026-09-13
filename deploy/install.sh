#!/bin/bash
# deploy/install.sh — put The Den on a Raspberry Pi (idempotent; re-run after `git pull`).
#
#   ./deploy/install.sh                 # venv + deps + config + systemd units, enable wildcat.target
#   ./deploy/install.sh --mosquitto     # also apt-install + enable the MQTT broker (Phase 1 step 5)
#   ./deploy/install.sh --journald      # also cap journald disk use (deploy/journald-wildcat.conf)
#   ./deploy/install.sh --no-start      # install/enable but don't (re)start anything now
#
# What it does, in order:
#   1. python venv at <repo>/venv, `pip install -e '.[den]'`
#   2. config/wildcat.toml: migrate bbs/config.ini if present, else copy the example (and stop)
#   3. `wildcat config validate` (a bad config aborts the install — fix it first)
#   4. render deploy/systemd/*.{service,target,timer} with the real paths → /etc/systemd/system
#   5. disable the LEGACY units (mesh-bbs, telemetry-logger, mesh-observatory) — two BBS
#      processes would fight over the node's single API socket
#   6. `systemctl enable wildcat.target` (+ start), then `wildcat doctor`
#
# Runs as your normal user; uses sudo only for the systemd/apt steps.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO/venv"
RUN_USER="${SUDO_USER:-$(id -un)}"
WITH_MOSQUITTO=0; WITH_JOURNALD=0; START=1
for a in "$@"; do
  case "$a" in
    --mosquitto) WITH_MOSQUITTO=1 ;;
    --journald)  WITH_JOURNALD=1 ;;
    --no-start)  START=0 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $a" >&2; exit 64 ;;
  esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] && { echo "run as your normal user (it will sudo where needed)" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd not found — this installer is for the Pi" >&2; exit 1; }

say "1/6 Python venv + dependencies  ($VENV)"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -e "$REPO[den]"

say "2/6 Config"
if [ -f "$REPO/config/wildcat.toml" ]; then
  echo "exists: $REPO/config/wildcat.toml"
elif [ -f "$REPO/bbs/config.ini" ]; then
  echo "migrating legacy bbs/config.ini → config/wildcat.toml"
  "$VENV/bin/wildcat" config migrate --write
else
  cp "$REPO/config/wildcat.example.toml" "$REPO/config/wildcat.toml"
  echo "created $REPO/config/wildcat.toml from the example — edit [radio] (host / type), then re-run."
  exit 3
fi

say "3/6 Validate"
"$VENV/bin/wildcat" config validate

if [ "$WITH_MOSQUITTO" = 1 ]; then
  say "Mosquitto (MQTT broker)"
  sudo apt-get install -y mosquitto mosquitto-clients
  sudo systemctl enable --now mosquitto
fi
if [ "$WITH_JOURNALD" = 1 ]; then
  say "journald cap"
  sudo mkdir -p /etc/systemd/journald.conf.d
  sudo cp "$REPO/deploy/journald-wildcat.conf" /etc/systemd/journald.conf.d/wildcat.conf
  sudo systemctl restart systemd-journald
fi

say "4/6 systemd units  (user=$RUN_USER repo=$REPO)"
TMP="$(mktemp -d)"
for f in "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.target "$REPO"/deploy/systemd/*.timer; do
  name="$(basename "$f")"
  sed -e "s|@REPO@|$REPO|g" -e "s|@VENV@|$VENV|g" -e "s|@USER@|$RUN_USER|g" "$f" > "$TMP/$name"
  if grep -q '@[A-Z]*@' "$TMP/$name"; then echo "unreplaced placeholder in $name" >&2; exit 1; fi
  sudo install -m 0644 "$TMP/$name" "/etc/systemd/system/$name"
  echo "installed /etc/systemd/system/$name"
done
rm -rf "$TMP"
sudo systemctl daemon-reload

say "5/6 Retire legacy units"
for u in mesh-bbs telemetry-logger mesh-observatory mesh-bbs-seth; do
  if systemctl list-unit-files "$u.service" --no-legend 2>/dev/null | grep -q "$u"; then
    echo "disabling legacy $u.service"
    sudo systemctl disable --now "$u.service" || true
  fi
done

say "6/6 Enable wildcat.target"
sudo systemctl enable wildcat.target wildcat-backup.timer
if [ "$START" = 1 ]; then
  sudo systemctl restart wildcat.target
  sleep 3
  systemctl --no-pager --no-legend list-units 'wildcat*' || true
fi

say "Doctor"
"$VENV/bin/wildcat" doctor || true

cat <<MSG

Done. Useful commands:
  systemctl status wildcat.target            # everything
  journalctl -u wildcat-bbs -f               # BBS log (also: wildcat-telemetry, wildcat-observatory, wildcat-meshd)
  sudo systemctl restart wildcat.target      # bounce the lot
  $VENV/bin/wildcat doctor                   # preflight
MSG

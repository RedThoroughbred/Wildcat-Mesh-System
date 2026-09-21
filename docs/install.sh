#!/usr/bin/env bash
# install.sh -- meshtasticd on a Raspberry Pi 3B + Waveshare SX1262 LoRaWAN/GNSS HAT
#
# Companion to docs/meshtasticd-pi-node.md. Run ON THE PI, as root (sudo).
# Written without access to the hardware -- read the runbook, and use
# DRY_RUN=1 first if you want to see every change before it happens.
#
# Usage:
#   sudo ./install.sh                 # phase 1: SPI/UART, repo, meshtasticd, HAT config
#   sudo reboot
#   sudo ./install.sh configure       # phase 2: region US915 + node name via CLI
#   sudo ./install.sh verify          # phase 3: health checks
#   sudo ./install.sh powersave       # optional: HDMI/LED/CPU/WiFi-powersave trims
#   sudo ./install.sh log2ram         # optional: logs in RAM (SD-card wear)
#
# Env overrides:
#   CHANNEL=beta            meshtastic apt channel: beta | alpha | daily
#   REGION=US               lora.region value
#   NODE_LONG="Wildcat Mesh 1"   NODE_SHORT="WM1"
#   DRY_RUN=1               print changes instead of making them
#   FORCE_NOT_PI=1          skip the "is this a Pi" check (testing only)
set -euo pipefail

CHANNEL="${CHANNEL:-beta}"
REGION="${REGION:-US}"
NODE_LONG="${NODE_LONG:-Wildcat Mesh 1}"
NODE_SHORT="${NODE_SHORT:-WM1}"
DRY_RUN="${DRY_RUN:-0}"
FORCE_NOT_PI="${FORCE_NOT_PI:-0}"

CONF_D=/etc/meshtasticd/config.d
HAT_YAML="$CONF_D/lora-waveshare-sx1262.yaml"
STAMP="$(date +%Y%m%d-%H%M%S)"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# run a command, or just show it under DRY_RUN=1
run() {
  if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] %s\n' "$*"; else "$@"; fi
}

need_root() {
  [ "$DRY_RUN" = 1 ] && return 0
  [ "$(id -u)" -eq 0 ] || die "run with sudo"
}

# Where config.txt lives: Bookworm+ = /boot/firmware, older = /boot
boot_cfg() {
  if [ -f /boot/firmware/config.txt ]; then echo /boot/firmware/config.txt
  elif [ -f /boot/config.txt ]; then echo /boot/config.txt
  else echo ""; fi
}

# idempotently make sure a line exists in config.txt
ensure_cfg_line() {
  local line="$1" cfg; cfg="$(boot_cfg)"
  [ -n "$cfg" ] || { warn "no config.txt found; skipping '$line'"; return 0; }
  if grep -qxF "$line" "$cfg" 2>/dev/null; then return 0; fi
  if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] append to $cfg: $line"; return 0; fi
  # put our lines under the [all] section marker we own, appended at the end
  echo "$line" >> "$cfg"
}

backup_cfg_once() {
  local cfg; cfg="$(boot_cfg)"
  [ -n "$cfg" ] || return 0
  [ -f "$cfg.wildcat-orig" ] || run cp -a "$cfg" "$cfg.wildcat-orig"
}

preflight() {
  say "Preflight"
  if [ "$FORCE_NOT_PI" != 1 ]; then
    grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null \
      || die "not a Raspberry Pi (set FORCE_NOT_PI=1 to override for testing)"
    echo "Model: $(tr -d '\0' < /proc/device-tree/model)"
  fi
  ARCH="$(dpkg --print-architecture 2>/dev/null || echo unknown)"
  . /etc/os-release
  CODENAME="${VERSION_CODENAME:-unknown}"
  OSNAME="${NAME:-unknown}"
  echo "OS: $OSNAME ($CODENAME), arch: $ARCH"
  case "$CODENAME" in bookworm|trixie) ;; *) die "unsupported Debian codename '$CODENAME' (need bookworm or trixie)";; esac
  case "$CHANNEL" in beta|alpha|daily) ;; *) die "CHANNEL must be beta|alpha|daily";; esac

  # Meshtastic publishes Raspbian_* packages for 32-bit armhf Pi OS and
  # Debian_* packages for everything else (e.g. 64-bit Pi OS).
  if [ "$ARCH" = "armhf" ] && [[ "$OSNAME" == Raspbian* ]]; then
    case "$CODENAME" in bookworm) REPO_OS="Raspbian_12";; trixie) REPO_OS="Raspbian_13";; esac
  else
    case "$CODENAME" in bookworm) REPO_OS="Debian_12";; trixie) REPO_OS="Debian_13";; esac
  fi
  echo "Meshtastic repo target: $REPO_OS (channel: $CHANNEL)"
}

enable_interfaces() {
  say "Enable SPI + hardware serial (GNSS) and free the CE0 pin for the HAT"
  backup_cfg_once
  # SPI on; I2C is NOT needed by this HAT (its GNSS is UART), but harmless.
  run raspi-config nonint do_spi 0
  # Serial: no login console, hardware UART on (L76K GNSS on the HAT)
  run raspi-config nonint do_serial_cons 1
  run raspi-config nonint do_serial_hw 0
  # HAT uses GPIO21 as chip-select (driven by meshtasticd), so the kernel must
  # not claim CE0. This overlay is what the community Waveshare script uses.
  ensure_cfg_line "dtoverlay=spi0-0cs"
}

add_repo_and_install() {
  say "Add Meshtastic apt repo ($CHANNEL / $REPO_OS) and install meshtasticd"
  local base="http://download.opensuse.org/repositories/network:/Meshtastic:/${CHANNEL}/${REPO_OS}"
  local keyurl="https://download.opensuse.org/repositories/network:Meshtastic:${CHANNEL}/${REPO_OS}/Release.key"
  local list="/etc/apt/sources.list.d/network:Meshtastic:${CHANNEL}.list"
  local gpg="/etc/apt/trusted.gpg.d/network_Meshtastic_${CHANNEL}.gpg"
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] write $list: deb $base/ /"
    echo "[dry-run] fetch key $keyurl -> $gpg"
  else
    echo "deb ${base}/ /" > "$list"
    curl -fsSL "$keyurl" | gpg --dearmor > "$gpg"
  fi
  run apt-get update
  run apt-get install -y meshtasticd curl ca-certificates
}

write_hat_config() {
  say "Write Waveshare SX1262 HAT config -> $HAT_YAML"
  run mkdir -p "$CONF_D"
  if [ -f "$HAT_YAML" ] && [ "$DRY_RUN" != 1 ]; then
    cp -a "$HAT_YAML" "$HAT_YAML.bak-$STAMP"; echo "backed up existing to $HAT_YAML.bak-$STAMP"
  fi
  local body
  body='# Waveshare SX1262 LoRaWAN/GNSS HAT (SPI) on a Raspberry Pi 3B
# Pin numbers are BCM GPIO numbers. Values mirror the community-tested config from
# github.com/FixedBit/meshtastic_scripts (example_config/config.yaml).
#
# If YOUR HAT differs (see docs/meshtasticd-pi-node.md, "Variants"), change Module/pins here.
Lora:
  Module: sx1262
  DIO2_AS_RF_SWITCH: true   # DIO2 drives the RF switch/RXEN on this HAT
  CS: 21                    # BCM21 (software CS; kernel CE0 released by dtoverlay=spi0-0cs)
  IRQ: 16                   # DIO1 -> BCM16
  Busy: 20                  # BUSY -> BCM20
  Reset: 18                 # RESET -> BCM18
  # If transmit range is terrible / no TX output, uncomment (TXEN is wired to BCM6):
  # SX126X_ANT_SW: 6

# Onboard L76K GNSS (GNSS variant of the HAT). Delete this block if your HAT has no GPS.
# Pi 3B: with Bluetooth enabled, the header UART is the mini-UART /dev/ttyS0.
GPS:
  SerialPath: /dev/ttyS0'
  if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] would write:"; echo "$body"; else printf '%s\n' "$body" > "$HAT_YAML"; fi
}

enable_service() {
  say "Enable meshtasticd at boot"
  run systemctl enable meshtasticd
  echo "It will (re)start after the reboot that applies the SPI/UART changes."
}

install_cli() {
  say "Install the Meshtastic CLI (pipx) for configure/verify"
  run apt-get install -y pipx python3-venv
  # system-wide so `sudo meshtastic` and your user both find it
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install meshtastic"
  else
    PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install --force "meshtastic[cli]" \
      || PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install --force meshtastic
  fi
}

phase_install() {
  need_root; preflight
  enable_interfaces
  add_repo_and_install
  write_hat_config
  enable_service
  install_cli
  say "Phase 1 done. REBOOT to apply SPI/UART changes:  sudo reboot"
  echo "Then run:  sudo ./install.sh configure   and   sudo ./install.sh verify"
}

phase_configure() {
  need_root
  say "Wait for meshtasticd's API on localhost:4403"
  if [ "$DRY_RUN" != 1 ]; then
    for i in $(seq 1 30); do
      (exec 3<>/dev/tcp/127.0.0.1/4403) 2>/dev/null && break
      sleep 2
      [ "$i" = 30 ] && die "meshtasticd not listening on 4403 -- run: sudo journalctl -u meshtasticd -n 50"
    done
  fi
  say "Set region $REGION and owner name"
  run meshtastic --host localhost --set lora.region "$REGION"
  sleep 5  # node reboots its config after a region change
  run meshtastic --host localhost --set-owner "$NODE_LONG" --set-owner-short "$NODE_SHORT"
  echo "Done. Verify with:  sudo ./install.sh verify"
}

phase_verify() {
  say "1/5 meshtasticd service"
  systemctl is-active meshtasticd && echo "OK: active" || { warn "not active"; journalctl -u meshtasticd -n 40 --no-pager || true; }
  say "2/5 SPI device present"
  ls -l /dev/spidev* 2>/dev/null || warn "no /dev/spidev* -- SPI not enabled or reboot pending"
  say "3/5 radio detected in logs"
  journalctl -u meshtasticd --no-pager -n 200 2>/dev/null \
    | grep -iE "sx126|radio|lora|region|init" | tail -15 || warn "no radio lines yet"
  say "4/5 API port 4403"
  (exec 3<>/dev/tcp/127.0.0.1/4403) 2>/dev/null && echo "OK: 4403 open" || warn "4403 closed"
  say "5/5 node info via CLI"
  meshtastic --host localhost --info 2>&1 | grep -iE "Owner|region|firmware|hwModel|Nodes in mesh" | head -10 \
    || warn "CLI could not read node"
  echo; echo "Peers heard so far (may take a few minutes on LongFast):"
  meshtastic --host localhost --nodes 2>&1 | head -20 || true
  echo; echo "Connect the phone app to:  $(hostname).local  (TCP 4403)  or  $(hostname -I 2>/dev/null | awk '{print $1}')"
}

phase_powersave() {
  need_root; say "Power trims (WiFi/BT left ON)"
  backup_cfg_once
  # LEDs (~5 mA each)
  ensure_cfg_line "dtparam=act_led_trigger=none"
  ensure_cfg_line "dtparam=act_led_activelow=off"
  ensure_cfg_line "dtparam=pwr_led_trigger=none"
  ensure_cfg_line "dtparam=pwr_led_activelow=off"
  ensure_cfg_line "dtparam=audio=off"
  # HDMI (~20-25 mA): on headless Lite the KMS display driver is not needed.
  local cfg; cfg="$(boot_cfg)"
  if [ -n "$cfg" ] && grep -qE '^dtoverlay=vc4-(f)?kms-v3d' "$cfg" && [ "$DRY_RUN" != 1 ]; then
    sed -i -E 's/^(dtoverlay=vc4-(f)?kms-v3d.*)$/#\1  # wildcat: headless power trim/' "$cfg"
    sed -i -E 's/^(max_framebuffers=.*)$/#\1  # wildcat: headless power trim/' "$cfg"
    echo "commented out vc4 KMS overlay (HDMI off next boot). Undo: restore $cfg.wildcat-orig"
  fi
  # CPU: cap at 600 MHz (meshtasticd is light); powersave-ish governor
  ensure_cfg_line "arm_freq=600"
  ensure_cfg_line "arm_freq_min=600"
  # WiFi radio power-save (WiFi stays ON; adds a little latency to inbound connections)
  if command -v nmcli >/dev/null && [ "$DRY_RUN" != 1 ]; then
    nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="802-11-wireless"{print $1}' | while read -r c; do
      nmcli connection modify "$c" 802-11-wireless.powersave 3 && echo "wifi powersave on for '$c'"
    done
  fi
  # Trim idle services (all optional; ignore if absent)
  for s in triggerhappy ModemManager apt-daily.timer apt-daily-upgrade.timer man-db.timer; do
    run systemctl disable --now "$s" 2>/dev/null || true
  done
  say "Reboot to apply. Bluetooth + WiFi were NOT disabled."
}

phase_log2ram() {
  need_root; say "Install log2ram (azlux repo)"
  . /etc/os-release
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] fetch keyring, write azlux.list for $VERSION_CODENAME, apt install log2ram"
  else
    wget -O /usr/share/keyrings/azlux-archive-keyring.gpg https://azlux.fr/repo.gpg
    echo "deb [signed-by=/usr/share/keyrings/azlux-archive-keyring.gpg] http://packages.azlux.fr/debian/ $VERSION_CODENAME main" \
      > /etc/apt/sources.list.d/azlux.list
    apt-get update && apt-get install -y log2ram
  fi
  echo "Default RAM log size is 128M of the Pi 3B's 1 GB (edit SIZE in /etc/log2ram.conf). REBOOT now."
}

case "${1:-install}" in
  install)   phase_install ;;
  configure) phase_configure ;;
  verify)    phase_verify ;;
  powersave) phase_powersave ;;
  log2ram)   phase_log2ram ;;
  *) echo "usage: sudo $0 [install|configure|verify|powersave|log2ram]"; exit 2 ;;
esac

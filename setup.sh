#!/bin/bash
# Wildcat Mesh System — setup (v2, "The Den")
#
#   ./setup.sh            # venv + deps + config/wildcat.toml + validate
#
# Idempotent: safe to re-run. Never touches an existing config/wildcat.toml.

set -e
cd "$(dirname "$0")"          # the repo root, wherever you ran this from

echo "╔══════════════════════════════════════════╗"
echo "║  Wildcat Mesh System Setup (The Den)     ║"
echo "╚══════════════════════════════════════════╝"
echo ""

echo "Checking Python version..."
python3 --version || { echo "Error: Python 3.9+ is required"; exit 1; }

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
else
    echo "Virtual environment already exists"
fi

echo "Installing the wildcat package + all service dependencies..."
./venv/bin/pip install --upgrade pip >/dev/null
./venv/bin/pip install -e '.[den]'

# ---- config ----------------------------------------------------------------
if [ -f "config/wildcat.toml" ]; then
    echo "Config exists: config/wildcat.toml"
elif [ -f "bbs/config.ini" ]; then
    echo ""
    echo "Found the legacy bbs/config.ini — migrating it to config/wildcat.toml"
    ./venv/bin/wildcat config migrate --write
else
    echo ""
    echo "Creating config/wildcat.toml"
    echo "Select your Meshtastic interface:"
    echo "1) USB/Serial (automatic detection)"
    echo "2) TCP/Network (ESP32 devices)"
    read -p "Enter choice (1 or 2): " interface_choice

    mkdir -p config
    if [ "$interface_choice" == "2" ]; then
        read -p "Enter device IP address (e.g., 192.168.1.100): " device_ip
        cat > config/wildcat.toml << TOML
# See config/wildcat.example.toml for every key and its default.
[radio]
type = "tcp"
host = "$device_ip"
TOML
        echo "✓ Configured for TCP at $device_ip"
    else
        cat > config/wildcat.toml << TOML
# See config/wildcat.example.toml for every key and its default.
[radio]
type = "serial"
# port = "/dev/ttyUSB0"   # omit to auto-detect a single USB node
TOML
        echo "✓ Configured for USB/Serial (auto-detect)"
    fi
fi

echo ""
./venv/bin/wildcat config validate

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Setup Complete!                         ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Check everything:   ./venv/bin/wildcat doctor        (Phase 1 step 3)"
echo "See the config:     ./venv/bin/wildcat config show"
echo ""
echo "Run manually (from ANY directory):"
echo "  ./venv/bin/python3 bbs/server.py              # BBS"
echo "  ./venv/bin/python3 bbs/telemetry_logger.py    # Telemetry logger"
echo "  ./venv/bin/python3 observatory/app.py         # Observatory"
echo ""
echo "Install as services:  see services/ (hardened units land in Phase 1 step 4)"

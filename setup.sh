#!/bin/bash
# Wildcat Mesh System Setup Script

set -e

echo "╔══════════════════════════════════════════╗"
echo "║  Wildcat Mesh System Setup               ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Check Python version
echo "Checking Python version..."
python3 --version || { echo "Error: Python 3 is required"; exit 1; }

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
else
    echo "Virtual environment already exists"
fi

# Activate and install dependencies
echo "Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r bbs/requirements.txt
pip install -r observatory/requirements.txt

# Create config.ini if it doesn't exist
if [ ! -f "bbs/config.ini" ]; then
    echo ""
    echo "Creating BBS configuration..."

    # Detect interface type
    echo "Select your Meshtastic interface:"
    echo "1) USB/Serial (automatic detection)"
    echo "2) TCP/Network (ESP32 devices)"
    read -p "Enter choice (1 or 2): " interface_choice

    if [ "$interface_choice" == "2" ]; then
        read -p "Enter device IP address (e.g., 192.168.1.100): " device_ip
        cat > bbs/config.ini << EOF
[interface]
type = tcp
hostname = $device_ip

[menu]
main_menu_items = W, N, R, Q, G, B, U, X
bbs_menu_items = M, B, C, J, X
utilities_menu_items = S, F, W, X
EOF

        # Update Observatory config
        sed -i "s/MESH_HOSTNAME = .*/MESH_HOSTNAME = '$device_ip'/" observatory/config.py
        echo "✓ Configured for TCP at $device_ip"
    else
        cat > bbs/config.ini << EOF
[interface]
type = serial

[menu]
main_menu_items = W, N, R, Q, G, B, U, X
bbs_menu_items = M, B, C, J, X
utilities_menu_items = S, F, W, X
EOF

        # Update Observatory config to serial
        sed -i "s/MESH_INTERFACE_TYPE = .*/MESH_INTERFACE_TYPE = 'serial'/" observatory/config.py
        sed -i "s/MESH_HOSTNAME = .*/# MESH_HOSTNAME = ''  # Not needed for serial/" observatory/config.py
        echo "✓ Configured for USB/Serial (auto-detect)"
    fi
else
    echo "Configuration already exists at bbs/config.ini"
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Setup Complete!                         ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "To run manually:"
echo "  source venv/bin/activate"
echo "  python3 bbs/server.py           # BBS"
echo "  python3 observatory/app.py      # Observatory"
echo ""
echo "To install as services:"
echo "  sudo cp services/*.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable mesh-bbs mesh-observatory"
echo "  sudo systemctl start mesh-bbs mesh-observatory"
echo ""

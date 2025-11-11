#!/bin/bash
# Wildcat Mesh Observatory - Installation Script

set -e

echo "=================================================="
echo "  🛰️  Wildcat Mesh Observatory Installer"
echo "=================================================="
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "❌ Please do NOT run this as root"
    exit 1
fi

# Install systemd service
echo "📦 Installing systemd service..."
sudo cp mesh-observatory.service /etc/systemd/system/
sudo systemctl daemon-reload

echo "✅ Service installed"
echo ""

# Enable and start service
read -p "Start service now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo systemctl enable mesh-observatory
    sudo systemctl start mesh-observatory
    echo "✅ Service started and enabled"
    echo ""
    echo "📊 Dashboard available at:"
    echo "   http://$(hostname -I | awk '{print $1}'):5001"
    echo ""
    echo "📋 Check status:"
    echo "   sudo systemctl status mesh-observatory"
    echo ""
    echo "📜 View logs:"
    echo "   journalctl -u mesh-observatory -f"
else
    echo "Service installed but not started."
    echo "To start manually: sudo systemctl start mesh-observatory"
fi

echo ""
echo "=================================================="
echo "  ✨ Installation complete!"
echo "=================================================="

#!/bin/bash
# Quick script to share Observatory with ngrok

echo "╔══════════════════════════════════════════╗"
echo "║  Wildcat Observatory Sharing             ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Check if ngrok is installed
if ! command -v ngrok &> /dev/null; then
    echo "⚠️  ngrok is not installed"
    echo ""
    echo "Install ngrok:"
    echo "  curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null"
    echo "  echo 'deb https://ngrok-agent.s3.amazonaws.com buster main' | sudo tee /etc/apt/sources.list.d/ngrok.list"
    echo "  sudo apt update && sudo apt install ngrok"
    echo ""
    echo "Then configure with your authtoken:"
    echo "  ngrok config add-authtoken YOUR_TOKEN"
    echo ""
    echo "Get a free token at: https://dashboard.ngrok.com/signup"
    exit 1
fi

# Check if Observatory is running
if ! lsof -i :5000 &> /dev/null; then
    echo "⚠️  Observatory is not running on port 5000"
    echo ""
    echo "Start it with:"
    echo "  sudo systemctl start mesh-observatory"
    echo "or manually:"
    echo "  source venv/bin/activate && python3 observatory/app.py"
    exit 1
fi

echo "✓ Observatory is running on port 5000"
echo ""

# Check if ngrok is already running
if curl -s http://localhost:4040/api/tunnels &> /dev/null; then
    echo "✓ ngrok tunnel is already active!"
    echo ""
    URL=$(curl -s http://localhost:4040/api/tunnels | grep -o '"public_url":"https://[^"]*' | cut -d'"' -f4 | head -1)
    if [ -n "$URL" ]; then
        echo "🌐 Public URL: $URL"
        echo ""
        echo "Share this URL with your friend!"
    else
        echo "Could not retrieve tunnel URL"
    fi
else
    echo "Starting ngrok tunnel..."
    echo ""

    # Start ngrok in background
    nohup ngrok http 5000 > /dev/null 2>&1 &
    NGROK_PID=$!

    # Wait for ngrok to start
    sleep 3

    # Get the public URL
    URL=$(curl -s http://localhost:4040/api/tunnels | grep -o '"public_url":"https://[^"]*' | cut -d'"' -f4 | head -1)

    if [ -n "$URL" ]; then
        echo "✅ Tunnel created successfully!"
        echo ""
        echo "╔══════════════════════════════════════════╗"
        echo "║  🌐 Public URL:                          ║"
        echo "║  $URL"
        echo "╚══════════════════════════════════════════╝"
        echo ""
        echo "Share this URL with your friend!"
        echo ""
        echo "To stop the tunnel:"
        echo "  pkill ngrok"
    else
        echo "❌ Failed to create tunnel"
        echo ""
        echo "Check if you've configured your authtoken:"
        echo "  ngrok config add-authtoken YOUR_TOKEN"
        echo ""
        echo "Get a free token at: https://dashboard.ngrok.com/signup"
        kill $NGROK_PID 2>/dev/null
        exit 1
    fi
fi

echo ""
echo "Tunnel will remain active. Monitor at: http://localhost:4040"

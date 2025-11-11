# Setting Up Remote Access with ngrok

This guide will help you expose your Wildcat Mesh Observatory to the internet so friends can access it remotely.

## Option 1: ngrok (Recommended)

ngrok creates a secure tunnel to your local server, giving you a public URL.

### Installation

1. **Install ngrok:**
   ```bash
   # For Raspberry Pi (ARM)
   curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
   echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
   sudo apt update
   sudo apt install ngrok
   ```

2. **Sign up for free account:**
   - Visit https://dashboard.ngrok.com/signup
   - Get your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken

3. **Configure ngrok:**
   ```bash
   ngrok config add-authtoken YOUR_AUTHTOKEN_HERE
   ```

### Usage

**Basic (temporary tunnel):**
```bash
ngrok http 5000
```

This will give you a URL like: `https://abc123.ngrok-free.app`

**Run in background:**
```bash
ngrok http 5000 > /dev/null &
```

**Check the tunnel URL:**
```bash
curl http://localhost:4040/api/tunnels | grep -o '"public_url":"[^"]*' | cut -d'"' -f4
```

### Persistent Setup (systemd service)

Create `/etc/systemd/system/ngrok.service`:
```ini
[Unit]
Description=ngrok tunnel for Wildcat Observatory
After=network.target mesh-observatory.service

[Service]
Type=simple
User=seth
ExecStart=/usr/local/bin/ngrok http 5000 --log stdout
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable ngrok.service
sudo systemctl start ngrok.service
```

Get your public URL:
```bash
curl http://localhost:4040/api/tunnels 2>/dev/null | grep -o '"public_url":"https://[^"]*' | cut -d'"' -f4
```

## Option 2: Cloudflare Tunnel (Free, More Reliable)

Cloudflare Tunnel is free and doesn't require a signup to ngrok.

### Installation

1. **Install cloudflared:**
   ```bash
   wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm
   sudo mv cloudflared-linux-arm /usr/local/bin/cloudflared
   sudo chmod +x /usr/local/bin/cloudflared
   ```

2. **Login to Cloudflare:**
   ```bash
   cloudflared tunnel login
   ```

3. **Create a tunnel:**
   ```bash
   cloudflared tunnel create wildcat-observatory
   ```

4. **Create config file** at `~/.cloudflared/config.yml`:
   ```yaml
   tunnel: YOUR_TUNNEL_ID
   credentials-file: /home/seth/.cloudflared/YOUR_TUNNEL_ID.json

   ingress:
     - hostname: your-subdomain.your-domain.com
       service: http://localhost:5000
     - service: http_status:404
   ```

5. **Run the tunnel:**
   ```bash
   cloudflared tunnel run wildcat-observatory
   ```

6. **Install as service:**
   ```bash
   sudo cloudflared service install
   sudo systemctl start cloudflared
   sudo systemctl enable cloudflared
   ```

## Option 3: Tailscale (VPN, Most Secure)

Perfect for sharing with trusted friends without exposing to the internet.

### Installation

1. **Install Tailscale:**
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   ```

2. **Connect:**
   ```bash
   sudo tailscale up
   ```

3. **Share your device:**
   - Your friend installs Tailscale
   - You share your Tailscale IP (e.g., `100.x.x.x`)
   - They access: `http://100.x.x.x:5000`

**Advantages:**
- Private VPN connection
- No public exposure
- Fast and secure
- Free for personal use

## Security Considerations

⚠️ **Important Notes:**

1. **ngrok free tier** shows an interstitial warning page
2. **Consider authentication** if exposing publicly
3. **Monitor access logs** in Observatory
4. **Use HTTPS** (ngrok provides this automatically)
5. **Limit who you share the URL with**

## Getting the Current Tunnel URL

If using ngrok, you can always check:
```bash
curl -s http://localhost:4040/api/tunnels | python3 -m json.tool
```

Or create a helper script:
```bash
#!/bin/bash
curl -s http://localhost:4040/api/tunnels | grep -o '"public_url":"https://[^"]*' | cut -d'"' -f4 | head -1
```

## Recommended for Your Use Case

For sharing with a friend, I recommend:
1. **Tailscale** - Most secure, private network
2. **ngrok** - Easiest setup, works instantly
3. **Cloudflare** - Best for long-term public hosting

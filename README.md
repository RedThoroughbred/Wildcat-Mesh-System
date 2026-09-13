# Wildcat Mesh System

A unified monorepo for the Wildcat Meshtastic ecosystem, combining the TC²-BBS (Bulletin Board System) and Mesh Observatory Dashboard.

## Overview

The Wildcat Mesh System provides comprehensive mesh network communication and monitoring tools:

- **TC²-BBS**: A text-based bulletin board system for Meshtastic networks, enabling offline messaging, bulletins, and channel management
- **Mesh Observatory**: A real-time web dashboard for monitoring network activity, telemetry, positions, and topology
- **Telemetry Logger**: A background service that captures telemetry, position, and neighbor data from the mesh network

All components share a common SQLite database for seamless integration.

## Repository Structure

```
Wildcat-Mesh-System/
├── wildcat/                # the v2 package: config, paths, CLI (meshd/bus/brain to come)
├── config/wildcat.toml     # THE config file (example: wildcat.example.toml)
├── docs/DECISIONS.md       # why things are the way they are
├── bbs/                    # TC²-BBS application
│   ├── server.py          # Main BBS server
│   ├── telemetry_logger.py # Telemetry capture service
│   ├── db_operations.py   # Database operations
│   ├── message_processing.py
│   ├── command_handlers.py
│   ├── utils.py
│   └── requirements.txt   # Python dependencies
├── observatory/           # Observatory Dashboard
│   ├── app.py            # Flask application
│   ├── config.py         # shim over config/wildcat.toml
│   ├── modules/          # Database and utilities
│   ├── templates/        # HTML templates
│   ├── static/           # CSS, JS, images
│   └── requirements.txt  # Python dependencies
├── shared/               # Shared resources
│   └── bulletins.db      # SQLite database (shared)
├── services/             # systemd service files
│   ├── mesh-bbs.service
│   ├── telemetry-logger.service
│   └── mesh-observatory.service
├── venv/                 # Shared Python virtual environment
└── docs/                 # Documentation
```

## Features

### TC²-BBS
- Private direct messaging between nodes
- Public bulletin boards (General, Info, Urgent)
- Channel URL directory
- Message persistence and sync across multiple BBS nodes
- Automatic urgent bulletin notifications
- Comprehensive message logging

### Mesh Observatory Dashboard
- **Real-time monitoring** via WebSockets
- **Network overview**: Active nodes, message counts, channel activity
- **Channel analytics**: Activity heatmaps, top senders, time-based filtering
- **Node details**: Individual node metrics, reliability stats, SNR/RSSI trends
- **BBS integration**: View messages, bulletins, and mail
- **Telemetry tracking**: Battery levels, temperature, uptime
- **Position logging**: GPS coordinates, altitude, ground speed
- **Topology mapping**: Neighbor information and network connectivity

### Telemetry Logger
- Captures device metrics (battery, voltage, channel utilization)
- Logs environmental data (temperature, humidity, pressure)
- Records GPS positions and movement
- Tracks neighbor relationships for topology analysis
- Runs independently alongside the BBS

## Installation

### Prerequisites
- Python 3.7+
- Meshtastic-compatible radio (connected via serial or TCP)
- systemd (for Linux service management)

### Quick Setup (Recommended)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/RedThoroughbred/Wildcat-Mesh-System.git
   cd Wildcat-Mesh-System
   ```

2. **Run the setup script:**
   ```bash
   ./setup.sh
   ```

   The setup script will:
   - Create a virtual environment
   - Install all dependencies
   - Detect your Meshtastic interface (USB/Serial or TCP)
   - Generate configuration files

### Manual Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/RedThoroughbred/Wildcat-Mesh-System.git
   cd Wildcat-Mesh-System
   ```

2. **Set up unified virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -e '.[den]'        # the wildcat package + every service dependency
   ```

3. **Configure (one file for everything):**
   ```bash
   cp config/wildcat.example.toml config/wildcat.toml   # edit [radio] at minimum
   wildcat config validate
   ```
   See **Configuration** below. (`observatory/config.py` is now a shim over the same file.)

### Running as Services

1. **Copy service files:**
   ```bash
   sudo cp services/*.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```

2. **Enable and start services:**
   ```bash
   # BBS
   sudo systemctl enable mesh-bbs.service
   sudo systemctl start mesh-bbs.service

   # Telemetry Logger
   sudo systemctl enable telemetry-logger.service
   sudo systemctl start telemetry-logger.service

   # Observatory Dashboard
   sudo systemctl enable mesh-observatory.service
   sudo systemctl start mesh-observatory.service
   ```

3. **Check status:**
   ```bash
   sudo systemctl status mesh-bbs
   sudo systemctl status telemetry-logger
   sudo systemctl status mesh-observatory
   ```

### Manual Running (Development)

**BBS:**
```bash
source venv/bin/activate
python3 bbs/server.py
```

**Telemetry Logger:**
```bash
source venv/bin/activate
python3 bbs/telemetry_logger.py
```

**Observatory:**
```bash
source venv/bin/activate
python3 observatory/app.py
```

Access the dashboard at: http://localhost:5000

## Database Schema

The shared `bulletins.db` contains:

### BBS Tables
- `bulletins`: Public bulletin board messages
- `mail`: Private direct messages
- `channels`: Channel URL directory
- `message_logs`: All message activity with SNR/RSSI

### Telemetry Tables
- `telemetry_logs`: Device and environmental metrics
- `position_logs`: GPS coordinates and movement
- `neighbor_info`: Network topology data
- `node_info`: Node metadata (name, hardware, firmware)

## Usage

### BBS Commands

Send commands via direct message to the BBS node:

- `M` - Main menu
- `CB` - Check bulletins
- `CB,,<board>` - List bulletins on board (e.g., CB,,General)
- `CB,<id>` - Read specific bulletin
- `CM` - Check mail
- `CM,<id>` - Read specific message
- `SM,<recipient>,<subject>,<message>` - Send mail
- `PB,<board>,<subject>,<message>` - Post bulletin

### Observatory Dashboard

Navigate to the web interface:

- **Dashboard**: Network overview with real-time stats
- **Channels**: Activity analysis with time-range filtering (24h/7d/30d)
- **Nodes**: Individual node details and reliability metrics
- **BBS Messages**: View all BBS activity and conversations

### Sharing Observatory with Remote Users

Want to let friends check out your Observatory from anywhere?

**Quick share with ngrok:**
```bash
./share-observatory.sh
```

This will give you a public URL like `https://abc123.ngrok-free.app` to share.

**Other options:**
- See [docs/NGROK_SETUP.md](docs/NGROK_SETUP.md) for detailed remote access guides
- Supports ngrok, Cloudflare Tunnel, and Tailscale VPN

## v2 — "The Den" (Phase 1)

The three scripts are becoming one system. What's in this branch:

- **`wildcat/`** — one package: validated config (`wildcat.toml`), `wildcat doctor`,
  `wildcat db backup`, the MQTT bus (`bus.py`), **meshd** (the single radio owner,
  `wildcat meshd`) and the `BusInterface` adapter that lets the BBS/telemetry run
  over the bus with a config flag.
- **`deploy/`** — hardened systemd units + `wildcat.target`, `install.sh`, nightly backup timer.
- **`docs/`** — `PHASE1_PLAN.md` (what/why/status), `DECISIONS.md` (the why-log),
  `MESHD_MIGRATION.md` (bus topics + switch-over), **`DEPLOY_PI.md` (run this on the Pi)**.
- `tests/` — 110+ tests, no radio needed: `.venv/bin/python -m pytest`.

```bash
./deploy/install.sh          # on the Pi: venv, config, units, doctor
wildcat doctor               # is everything OK?
```

## Configuration

**One file: `config/wildcat.toml`** (v2 / "The Den" — see `docs/DECISIONS.md`).
It replaces `bbs/config.ini` *and* the constants that used to live in
`observatory/config.py`. Every service reads it through `wildcat/config.py`,
which validates it at boot and reports problems by `[table].key`.

```bash
cp config/wildcat.example.toml config/wildcat.toml   # annotated, every key + default
./venv/bin/wildcat config validate                  # exit 0 = good; exit 2 = says what's wrong
./venv/bin/wildcat config show                      # the fully-resolved config (secrets redacted)
./venv/bin/wildcat config path                      # the search order + which file wins
```

Minimal example:

```toml
[radio]
type = "tcp"             # or "serial" (USB, auto-detects a single node)
host = "192.168.86.37"   # tcp only

[bbs]
sync_nodes = ["!17d7e4b7"]   # other TC²-BBS nodes to sync with (optional)
# [bbs.menu] omitted → the stock Wildcat menu. No more KeyError.

[observatory]
port = 5000
```

Where the file is looked for, in order: `--config` → `$WILDCAT_CONFIG` →
`<repo>/config/wildcat.toml` → `/etc/wildcat/wildcat.toml` → legacy
`<repo>/bbs/config.ini`. Nothing depends on the current directory anymore —
`python3 bbs/server.py` works from `/`, from `bbs/`, or from systemd.

**Still on the old `bbs/config.ini`?** It keeps working (it's read through a
migrator, with a warning). Convert it once:

```bash
./venv/bin/wildcat config migrate --write   # writes config/wildcat.toml, verifies it
```

## Development

### Database Migrations

Both BBS and Observatory initialize their tables automatically on first run. If you need to manually initialize:

```python
from db_operations import initialize_database
initialize_database()
```

### Adding Custom Commands

Edit `bbs/command_handlers.py` to add new BBS commands.

### Extending Dashboard

Add new routes in `observatory/app.py` and corresponding templates in `observatory/templates/`.

## Contributing

This is a custom fork of the TC2-BBS project with significant Observatory and telemetry enhancements. See individual file headers for attribution.

## Credits

- **TC2-BBS**: Original BBS system by [TheCommsChannel](https://github.com/TheCommsChannel/TC2-BBS-mesh)
- **Observatory Dashboard**: Custom development by Wildcat Mesh System
- **Telemetry Logger**: Custom development for mesh network monitoring

## License

See LICENSE file for details.

## Support

For issues, questions, or contributions, please open an issue on the GitHub repository.

---

**Wildcat TC² BBS & Mesh Observatory** - Bringing comprehensive communication and monitoring to Meshtastic mesh networks.

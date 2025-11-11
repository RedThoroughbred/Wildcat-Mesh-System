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
├── bbs/                    # TC²-BBS application
│   ├── server.py          # Main BBS server
│   ├── telemetry_logger.py # Telemetry capture service
│   ├── db_operations.py   # Database operations
│   ├── message_processing.py
│   ├── command_handlers.py
│   ├── utils.py
│   ├── config.ini         # BBS configuration
│   └── venv/              # Python virtual environment
├── observatory/           # Observatory Dashboard
│   ├── app.py            # Flask application
│   ├── config.py         # Dashboard configuration
│   ├── modules/          # Database and utilities
│   ├── templates/        # HTML templates
│   ├── static/           # CSS, JS, images
│   └── venv/             # Python virtual environment
├── shared/               # Shared resources
│   └── bulletins.db      # SQLite database (shared)
├── services/             # systemd service files
│   ├── mesh-bbs.service
│   ├── telemetry-logger.service
│   └── mesh-observatory.service
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

### Setup

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd Wildcat-Mesh-System
   ```

2. **Set up BBS:**
   ```bash
   cd bbs
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Set up Observatory:**
   ```bash
   cd ../observatory
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Configure BBS:**
   Edit `bbs/config.ini` with your Meshtastic interface settings:
   ```ini
   [interface]
   type = serial  # or tcp
   # hostname = 192.168.1.100  # if using tcp
   ```

5. **Configure Observatory:**
   Edit `observatory/config.py` if needed (default: TCP at 192.168.86.37)

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
cd bbs
source venv/bin/activate
python3 server.py
```

**Telemetry Logger:**
```bash
cd bbs
source venv/bin/activate
python3 telemetry_logger.py
```

**Observatory:**
```bash
cd observatory
source venv/bin/activate
python3 app.py
```

Access the dashboard at: http://localhost:5001

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

## Configuration

### BBS (`bbs/config.ini`)
```ini
[interface]
type = serial  # or tcp
# hostname = 192.168.1.100  # for TCP mode

[sync]
bbs_nodes = !12345678,!87654321  # Other BBS nodes to sync with
```

### Observatory (`observatory/config.py`)
```python
# Database path (automatically configured for monorepo)
DATABASE_PATH = '../shared/bulletins.db'

# Meshtastic connection
MESH_INTERFACE_TYPE = 'tcp'
MESH_HOSTNAME = '192.168.86.37'

# Dashboard settings
REFRESH_INTERVAL = 5  # seconds
PORT = 5001
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

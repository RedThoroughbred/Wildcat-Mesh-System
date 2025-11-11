```markdown

# Meshtastic Pi Hub Project - Claude Context

  

## Project Overview

Building a Raspberry Pi-based Meshtastic automation hub that combines logging, BBS functionality, and remote node administration capabilities for the Northern Kentucky mesh network.

  

## Current Hardware Setup

  

### Raspberry Pi

- **Location:** Home in Northern Kentucky

- **Storage:** 128GB microSD card (cloned from 32GB, filesystem expanded)

- **User:** seth

- **Hostname:** nicho

- **Network:** Connected via Ethernet and WiFi to home network (192.168.86.x)

- **Remote Access:** Planning Tailscale/VPN for remote management

  

### Connected Meshtastic Nodes

  

**1. Heltec V3**

- **Node ID:** !b03d4b80

- **Name:** KR4DES - SV

- **Short Name:** 4B80

- **Role:** CLIENT (will change to CLIENT_MUTE for BBS)

- **Connection:** USB to Pi (`/dev/ttyUSB0` - CP210x UART Bridge)

- **Alternate:** TCP via WiFi at 192.168.86.37

- **Public Key:** `sJfg1V2kS3wCWO2VkXeCw+Uh1Laq0/dw5cTpaxDBP3U=`

- **Status:** Currently connected via USB for testing

  

**2. Station G2 (SouthernWoods G2)**

- **Node ID:** !a2e4ff64

- **Name:** SouthernWoods G2

- **Short Name:** SWG2

- **Role:** CLIENT_BASE

- **Connection:** TCP via Ethernet at 192.168.86.34

- **Hardware:** STATION_G2 (designed for 24/7 base station operation)

- **Advantage:** External antenna connector, better range

- **Public Key:** `1AbVA4cnIt2ipseYzHwRULrnNjfxsXXOavajnrx2uhA=`

- **Status:** Primary home base station

  

**3. T1000-E Tracker**

- **Node ID:** !b33d44f9

- **Name:** rectangle square

- **Short Name:** rstk

- **Role:** TRACKER

- **Status:** Mobile tracker device

  

## Network Context

  

### Active Mesh Network

- **24+ nodes visible** on local mesh

- **Channels:**

- PRIMARY: "PrivateChat" (encrypted)

- SECONDARY: "LongFast" (default/public)

- SECONDARY: "cincymesh" (encrypted community channel)

- SECONDARY: "TaylorMt" (encrypted)

- SECONDARY: "Kentucky" (encrypted)

  

### Mesh Community

- Part of Northern Kentucky / Cincinnati mesh network (cincymesh)

- Working to expand network with monthly node additions

- Interest in connecting/managing community nodes

  

## Current Implementation Status

  

### ✅ Completed: Mesh Logger

  

**Location:** `/home/seth/mesh_logger.py`

  

**Functionality:**

- Connects to Meshtastic node via USB serial

- Logs all received mesh packets to daily JSON files

- Captures: NodeInfo, Position, Telemetry, Text Messages

- Logs saved to: `/home/seth/mesh_logs/mesh_YYYY-MM-DD.log`

  

**Current Connection:** USB to Heltec V3 at `/dev/ttyUSB0`

  

**Script Status:** Working and tested (manually run)

  

**To-Do:**

- Set up as systemd service for auto-start

- Switch to TCP connection (192.168.86.37) to free USB for BBS

- Add log rotation/cleanup for 128GB storage management

  

### ✅ Completed: Wildcat TC² BBS System



**Repository:** https://github.com/RedThoroughbred/Wildcat-TC2-BBS.git

**Based on:** https://github.com/TheCommsChannel/TC2-BBS-mesh



**Configuration:**

- **Run on:** Heltec V3 (!b03d4b80 - KR4DES - SV) via TCP 192.168.86.37

- **Node Role:** CLIENT_MUTE (receives all messages, minimal rebroadcast)

- **Primary Channel:** LongFast (public - for discoverability)

- **Location:** `/home/seth/Wildcat-TC2-BBS`

- **Status:** Running as systemd service (mesh-bbs.service)

- **Auto-restart:** Every 10 minutes via watchdog to prevent serial connection issues



**Custom Features Added:**

- 🌤️ **Interactive Weather** - Users enter ZIP code for live local weather (OpenWeatherMap API)

- 📡 **Network Info Menu** - Live mesh stats (nodes online, signal reports, hardware breakdown)

- 📚 **Resources Menu** - Meshtastic guides, hardware recommendations, official docs

- 🤖 **NotebookLM AI Guide** - https://notebooklm.google.com/notebook/ebf5b4fd-2074-4160-a9c9-996155209bb8

- 💬 **25+ Mesh Quotes** - Inspirational messages about decentralized networks

- 🏷️ **Custom Branding** - "Wildcat TC² BBS" for Northern Kentucky

- 🎮 **Trivia Game** - Meshtastic knowledge quiz from trivia.txt

- 📊 **Telemetry Logger** - Background service logging all mesh telemetry to SQLite (telemetry-logger.service)

- 💾 **Enhanced Database** - Channel activity tracking, message logging with SNR/RSSI data

  

**Bulletin Boards:**

1. General - Casual chat, announcements

2. Info - Important information, guides

3. News - Mesh network news, updates

4. Urgent - Emergency/critical info (admin-only posting)

  

**Admin Access:**

- Primary: !b03d4b80 (your admin node - update this to correct node ID)

  

**How to Use:**

- Send DM to !9e766b18 to access BBS

- Press letter in brackets to navigate (W=Weather, N=Network Info, etc.)

- Works from any Meshtastic device on the mesh

  

**Known Issue:**

- Serial connection blocks when connecting via Bluetooth/iPad to configure Heltec

- Solution: BBS auto-restarts every 10 minutes OR manually restart: `sudo systemctl restart mesh-bbs.service`

- Future: Set up MQTT to allow simultaneous connections

  

### ✅ Completed: Wildcat Mesh Network Observatory



**Repository:** `/home/seth/mesh-observatory` (local - needs GitHub repo created)

**Purpose:** Real-time monitoring and analytics dashboard for Northern Kentucky mesh network

**Access:** http://192.168.86.141:5001 (or http://nicho:5001)



**Features:**

- 📊 **Live Dashboard** - Real-time stats with WebSocket updates (● LIVE indicator)

- 🗺️ **Interactive Map** - Leaflet.js map centered on Walton, KY showing node locations

- 📡 **Node Directory** - Complete list of 7 discovered nodes with SNR/RSSI stats

- 📈 **Node Details** - Individual node pages with message history and dual-axis charts

- 🌐 **Propagation Analysis** - 24-hour SNR trends, best/worst connections, distribution graphs

- 💬 **Channel Activity** - Message volume and activity across different channels

- 📥 **CSV Export** - Download nodes list and message history (last 1000)

- 📚 **API Documentation** - RESTful JSON API with interactive test buttons (`/api/v1/*`)

- ⚙️ **Admin Panel** - Export tools, future BBS config management



**Technology Stack:**

- **Backend:** Flask + Flask-SocketIO (Python 3.9)

- **Database:** SQLite (shared with BBS - bulletins.db)

- **Frontend:** Vanilla JS, Chart.js, Leaflet.js

- **Status:** Running manually (need systemd service)



**API Endpoints:**

- `GET /api/v1/stats` - Overall mesh statistics

- `GET /api/v1/nodes` - Active nodes list

- `GET /api/v1/messages` - Recent messages

- `GET /export/nodes.csv` - Node data export

- `GET /export/messages.csv` - Message history export



### 📋 Planned: Remote Node Administration



**Purpose:** Manage deployed field nodes without physical access



**Method (Firmware 2.5+):**

- Use public key cryptography

- Store admin node's public key in remote node's Security Config

- Up to 3 admin keys per node



**Admin Node:** Station G2 (home base) will hold admin keys for deployed nodes



**Use Cases:**

- Configure new nodes after deployment

- Adjust settings on field nodes

- Monitor node health remotely

- Update roles/channels as needed



**Implementation:** Via CLI or mobile app, not web client (web doesn't support remote admin commands yet)

  

## Architecture Plan



```

Raspberry Pi (192.168.86.141 - nicho)

│

├── BBS Server Service (mesh-bbs.service)

│ ├── Connection: TCP → Heltec V3 (192.168.86.37)

│ ├── Purpose: Community bulletin board + interactive features

│ ├── Software: Wildcat TC²-BBS

│ ├── Database: /home/seth/Wildcat-TC2-BBS/bulletins.db

│ └── Status: systemd service, auto-restart every 10min

│

├── Telemetry Logger Service (telemetry-logger.service)

│ ├── Connection: TCP → Heltec V3 (192.168.86.37)

│ ├── Purpose: Log all mesh telemetry, node info, positions

│ ├── Database: Writes to bulletins.db (shared with BBS)

│ └── Status: systemd service, background daemon

│

├── Mesh Observatory Web Dashboard (needs service)

│ ├── Connection: Reads from bulletins.db

│ ├── Purpose: Real-time monitoring, analytics, propagation analysis

│ ├── Software: Flask + SocketIO

│ ├── Access: http://192.168.86.141:5001

│ └── Status: Running manually (TODO: create systemd service)

│

└── Remote Access

├── SSH: Via WiFi on local network (shrinix)

└── Future: Tailscale VPN for remote access

```



**Current Services:**

- `mesh-bbs.service` - BBS server with watchdog restart

- `telemetry-logger.service` - Background telemetry collection

- Observatory - Manual start (needs service file)

  

## Hardware Expansion Plans

  

### Yagi Antenna Project

- **Frequency:** 915 MHz

- **Purpose:** Directional gain for reaching distant nodes

- **Mount:** Connect to Heltec V3 or Station G2

- **Design:** Custom build based on calculated element lengths

  

### Future Node Deployments

- Monthly addition of new nodes to expand network

- Strategic placement for community coverage

- Remote management via admin keys

  

## User Context (Seth)

  

### Background

- Software developer with Flask/Python experience

- Learning electronics, RF technology, ham radio (studying for license)

- Built RF research lab "PiFlip" with RTL-SDR, CC1101, PN532

- Experience with n8n automation on Google Cloud Platform

- Interested in mesh networking and decentralized communication

- Author of "The AI Choice: Into the Light, Into the Dark" (launches Nov 14, 2025)

  

### Related Skills

- Raspberry Pi projects

- Arduino and embedded systems

- Network security (Wireshark analysis)

- Home automation and monitoring

- 3D printing and antenna design

- Zen Buddhism and recovery principles

  

### Current Learning Goals

- Master Meshtastic mesh networking

- Optimize node configuration and placement

- Build community mesh infrastructure

- Ham radio licensing

- RF propagation and antenna theory

  

## Technical Notes

  

### Python Environment

- **Python Version:** 3.9

- **Meshtastic Library:** Installed via pip3

- **Key Dependencies:** meshtastic, pubsub, configparser

  

### Serial Device Detection

- Heltec V3 appears as `/dev/ttyUSB0` (Silicon Labs CP210x)

- Check with: `ls -l /dev/ttyUSB*` or `lsusb`

  

### Meshtastic CLI Commands

```bash

# Connect via USB

meshtastic --info

  

# Connect via TCP

meshtastic --host 192.168.86.37 --info

meshtastic --host 192.168.86.34 --info

  

# Change node role

meshtastic --set device.role CLIENT_MUTE

  

# Get public key for admin setup

meshtastic --info | grep publicKey

```

  

### Service Management

```bash

# Create service

sudo nano /etc/systemd/system/service-name.service

  

# Enable and start

sudo systemctl daemon-reload

sudo systemctl enable service-name.service

sudo systemctl start service-name.service

  

# Check status

sudo systemctl status service-name.service

  

# View logs

sudo journalctl -u service-name.service -f

```

  

## Open Questions for Implementation

  

1. **BBS Configuration:**

- Final BBS name? (e.g., "NKY-BBS", "KR4DES-BBS")

- Public or invite-only access?

- Message retention period?

- Enable BBS-to-BBS sync with other nodes?

  

2. **Resource Allocation:**

- Should both services (logger + BBS) run 24/7?

- Priority if system resources become constrained?

  

3. **Future AI Integration:**

- Auto-summarize daily mesh activity?

- Answer common Meshtastic questions?

- Generate propagation reports?

- Moderate message boards?

  

4. **Remote Access:**

- Tailscale vs. other VPN solutions?

- Need for external SSH access?

  

## References

  

### Documentation

- Meshtastic Docs: https://meshtastic.org/docs/

- Remote Admin: https://meshtastic.org/docs/configuration/remote-admin/

- TC²-BBS: https://github.com/TheCommsChannel/TC2-BBS-mesh

- Python API: https://python.meshtastic.org/

  

### Community

- CincyMesh: Local Northern Kentucky / Cincinnati mesh network

- Various mesh operators visible on network (24+ nodes)

  

## Next Steps

  

1. **Immediate:** Set up BBS on Station G2

- Clone repository

- Configure settings

- Test functionality

- Deploy as service

  

2. **Short-term:** Convert logger to systemd service

- Switch to TCP connection

- Enable auto-start

- Test reliability

  

3. **Medium-term:** Remote admin setup

- Configure admin keys on deployed nodes

- Test remote management

- Document procedures

  

4. **Long-term:** Flask web interface

- Live mesh monitoring

- Remote admin GUI

- Log analytics

- n8n integration

  

---

  

*Last Updated: 2025-11-09*

*Location: /home/seth/claude.md*

```

  

This file gives Claude Code (or any AI assistant) full context about your project, what's been completed, what's in progress, and what's planned. Save it as `/home/seth/claude.md` on your Pi and Claude Code will be able to jump right in with full understanding of your setup!



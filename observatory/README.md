# 🛰️ Wildcat Mesh Network Observatory

**Modern, dark-themed dashboard for your Northern Kentucky mesh network**

![Status](https://img.shields.io/badge/status-ready-success)
![Python](https://img.shields.io/badge/python-3.9-blue)
![Flask](https://img.shields.io/badge/flask-3.0-green)

---

## ✨ Features

- 📊 **Live Dashboard** - Real-time mesh statistics
- 🗺️ **Interactive Map** - Node locations and coverage
- 📡 **Node Tracking** - Monitor all nodes in your network
- 📈 **Propagation Analysis** - SNR/RSSI trends and best times
- 💬 **Channel Activity** - Message analytics by channel
- ⚙️ **Admin Tools** - Network management interface

## 🎨 Design

- **Dark theme** optimized for low-light viewing
- **Responsive** - Works on desktop, tablet, and mobile
- **Modern UI** - Clean cards and beautiful charts
- **Real-time updates** - Auto-refresh every 30 seconds

## 🚀 Quick Start

### Manual Start (Testing)
```bash
cd /home/seth/mesh-observatory
source venv/bin/activate
python3 app.py
```

Then open: **http://192.168.86.141:5001**

### Systemd Service (Production)
```bash
# Install service
sudo cp mesh-observatory.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mesh-observatory
sudo systemctl start mesh-observatory

# Check status
sudo systemctl status mesh-observatory

# View logs
journalctl -u mesh-observatory -f
```

## 📁 Project Structure

```
mesh-observatory/
├── app.py                  # Main Flask application
├── config.py               # Configuration
├── requirements.txt        # Python dependencies
├── static/
│   ├── css/
│   │   └── dashboard.css  # Dark theme styles
│   └── js/
├── templates/
│   ├── base.html          # Base template
│   └── dashboard.html     # Main dashboard
└── modules/
    └── db.py              # Database operations
```

## 🗄️ Database

Uses the same SQLite database as the BBS: `/home/seth/Wildcat-TC2-BBS/bulletins.db`

**New tables created:**
- `telemetry_logs` - Battery, temperature, voltage history
- `position_logs` - GPS tracking
- `neighbor_info` - Network topology
- `admin_events` - Administrative actions
- `node_info` - Node metadata

**Existing tables (read-only):**
- `message_logs` - Message history with SNR/RSSI
- `bulletins` - BBS bulletins
- `mail` - BBS mail

## 🔒 Safety Features

✅ **Completely separate from BBS**
- Runs on different port (5001 vs Meshtastic connection)
- No modifications to BBS code
- Read-only access to existing BBS data
- Creates its own tables without touching BBS tables

✅ **Non-intrusive**
- Won't interfere with running BBS
- Can be stopped/started independently
- No risk of breaking BBS functionality

## 🎯 Next Steps

### Phase 2: Enhanced Data Collection
1. Create `telemetry_logger.py` service
2. Capture TELEMETRY_APP packets
3. Capture POSITION_APP packets
4. Log node topology

### Phase 3: Advanced Features
1. Real-time WebSocket updates
2. Interactive Leaflet.js map
3. Advanced propagation charts
4. Alert system (low battery, offline nodes)
5. Admin tools (send messages, export data)

## 🛠️ Tech Stack

**Backend:**
- Flask 3.0
- SQLite3
- Python 3.9

**Frontend:**
- Custom dark CSS theme
- Chart.js for graphs
- Leaflet.js for maps (coming soon)
- Socket.IO for real-time updates (coming soon)

## 📊 Current Features

### Dashboard (/)
- Active node count (last hour)
- 24h message count
- Average SNR
- Recent activity feed
- Channel activity chart
- Low battery alerts

### Map (/map)
*Coming soon*

### Nodes (/nodes)
*Coming soon*

### Propagation (/propagation)
*Coming soon*

### Channels (/channels)
*Coming soon*

### Admin (/admin)
*Coming soon*

## 🔧 Configuration

Edit `config.py` to customize:
- Dashboard refresh interval
- Theme colors
- Database path
- Port number

## 🐛 Troubleshooting

**Port already in use:**
```bash
# Change PORT in config.py
PORT = 5001  # or any other free port
```

**Database not found:**
```bash
# Verify path in config.py
DATABASE_PATH = '/home/seth/Wildcat-TC2-BBS/bulletins.db'
```

**No data showing:**
- Make sure BBS is running and logging messages
- Check that message_logs table has data:
  ```bash
  sqlite3 /home/seth/Wildcat-TC2-BBS/bulletins.db "SELECT COUNT(*) FROM message_logs"
  ```

## 📝 Development Notes

**Current Sprint:** Phase 1 - Basic Dashboard ✅

**Next Sprint:** Phase 2 - Data Collection
- Build telemetry logger
- Capture all packet types
- Populate new database tables

**Future Sprints:**
- Real-time updates via WebSocket
- Interactive maps
- Advanced analytics
- Home Assistant integration
- n8n workflows

---

**Ready to explore your mesh network! 🚀**

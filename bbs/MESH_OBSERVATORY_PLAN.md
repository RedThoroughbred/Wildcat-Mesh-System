# 🛰️ Wildcat Mesh Network Observatory - Development Plan

**Status:** Ready to implement
**Date:** 2025-11-09
**Location:** Northern Kentucky mesh network

---

## 📋 Phase 1: Enhanced Data Collection (Week 1)

### Database Schema Expansion

**New Tables to Add:**

```sql
-- Node telemetry history
CREATE TABLE telemetry_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    node_name TEXT,
    battery_level INTEGER,
    voltage REAL,
    channel_util REAL,
    air_util_tx REAL,
    temperature REAL,
    humidity REAL,
    pressure REAL,
    gas_resistance INTEGER,
    uptime_seconds INTEGER
);

-- Position history for tracking
CREATE TABLE position_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    node_name TEXT,
    latitude REAL,
    longitude REAL,
    altitude INTEGER,
    precision_bits INTEGER,
    ground_speed INTEGER,
    ground_track INTEGER,
    satellites_in_view INTEGER
);

-- Network topology (who hears who)
CREATE TABLE neighbor_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    neighbor_id TEXT NOT NULL,
    snr REAL,
    last_heard INTEGER
);

-- Administrative events
CREATE TABLE admin_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    node_id TEXT,
    admin_id TEXT,
    description TEXT,
    success BOOLEAN
);

-- Node metadata (current state)
CREATE TABLE node_info (
    node_id TEXT PRIMARY KEY,
    short_name TEXT,
    long_name TEXT,
    hw_model TEXT,
    role TEXT,
    firmware_version TEXT,
    first_seen INTEGER,
    last_seen INTEGER,
    is_favorite BOOLEAN DEFAULT 0,
    notes TEXT
);
```

### Message Processing Updates

**Enhance `message_processing.py` to capture:**
- TELEMETRY_APP packets → telemetry_logs
- POSITION_APP packets → position_logs
- NODEINFO_APP packets → node_info updates
- NEIGHBORINFO_APP packets → neighbor_info
- ADMIN_APP responses → admin_events

**New file:** `telemetry_logger.py`
- Dedicated telemetry collection service
- Runs alongside BBS
- Subscribes to all packet types
- Batch writes to database

---

## 📊 Phase 2: Flask Dashboard Foundation (Week 1-2)

### Project Structure

```
mesh-observatory/
├── app.py                 # Main Flask application
├── config.py              # Configuration
├── requirements.txt       # Flask, plotly, folium, etc.
├── static/
│   ├── css/
│   │   └── dashboard.css
│   ├── js/
│   │   ├── map.js        # Leaflet map
│   │   ├── charts.js     # Chart.js graphs
│   │   └── live.js       # WebSocket updates
│   └── img/
├── templates/
│   ├── base.html
│   ├── dashboard.html    # Main overview
│   ├── nodes.html        # Node list & details
│   ├── propagation.html  # Signal analysis
│   ├── map.html          # Coverage map
│   ├── admin.html        # Admin tools
│   └── reports.html      # Generated reports
└── modules/
    ├── db.py             # Database queries
    ├── mesh_api.py       # Meshtastic interface wrapper
    └── analytics.py      # Data analysis functions
```

### Dashboard Pages - MVP

**1. Dashboard (Home) - `/`**
- Live node count (online in last hour)
- 24h message count
- Current mesh health indicator
- Recent activity feed (last 20 messages)
- Mini map with active nodes
- Quick stats cards (avg SNR, battery alerts)

**2. Live Map - `/map`**
- Leaflet.js interactive map
- Markers for all nodes with GPS
- Color-coded by signal strength
- Click marker → node popup with stats
- Range circles (estimated coverage)
- Heatmap toggle for signal strength

**3. Nodes List - `/nodes`**
- Sortable table: Name, Model, Role, Battery, SNR, Last Seen
- Filter by: Online/Offline, Hardware, Role
- Click row → detailed node view
- Export to CSV

**4. Node Detail - `/node/<node_id>`**
- Node information card
- Battery history graph (7 days)
- SNR trend chart (7 days)
- Position history map
- Message count timeline
- Neighbor list (who it hears)

**5. Propagation Analysis - `/propagation`**
- Hourly SNR chart (find best times)
- Best/worst connections table
- Weather correlation (future: fetch weather API)
- Distance vs SNR scatter plot
- Antenna comparison tool

**6. Channel Activity - `/channels`**
- Messages per channel pie chart
- Top senders bar chart
- Message timeline (24h, 7d, 30d)
- Peak activity hours

**7. Admin Tools - `/admin`** (protected route)
- Send broadcast message
- Remote node config viewer
- Export full database
- Generate reports
- View system logs

### Technology Stack

**Backend:**
- Flask 3.0
- SQLite3 (bulletins.db)
- Flask-SocketIO (real-time updates)
- APScheduler (background jobs)

**Frontend:**
- Bootstrap 5 (UI framework)
- Leaflet.js (maps)
- Chart.js (graphs)
- DataTables (sortable tables)
- Socket.IO (live updates)

**Optional:**
- Flask-Login (auth system)
- Plotly (advanced charts)
- Folium (Python map generation)

---

## 🔧 Phase 3: Advanced Features (Week 3+)

### Real-Time Updates
- WebSocket connection from dashboard
- Live node status changes
- New message notifications
- Alert system (low battery, node offline)

### Automated Reports
- Daily email digest (via n8n?)
- Weekly propagation report
- Monthly mesh health summary
- PDF export with charts

### API Endpoints
- RESTful API for external tools
- `/api/v1/nodes` - Get all nodes
- `/api/v1/stats` - Get mesh statistics
- `/api/v1/messages` - Query message logs
- Webhook support for n8n integration

### Alerts & Monitoring
- Battery below 20% → notification
- Node offline > 1 hour → alert
- Unusual SNR drop → investigation
- New node discovered → welcome message

### Advanced Analytics
- Machine learning propagation prediction
- Anomaly detection (unusual patterns)
- Network health scoring algorithm
- Optimal repeater placement suggestions

---

## 🎯 Implementation Priority

### Sprint 1 (This Week)
1. ✅ Create database schema for telemetry/position/topology
2. ✅ Build telemetry collection service
3. ✅ Create basic Flask app structure
4. ✅ Implement dashboard homepage
5. ✅ Build live map page

### Sprint 2 (Next Week)
1. Node list & detail pages
2. Propagation analysis visualizations
3. Channel activity charts
4. Basic admin tools
5. CSS styling & mobile responsive

### Sprint 3 (Week 3)
1. Real-time WebSocket updates
2. API endpoints
3. Alert system
4. Report generation
5. User authentication

---

## 📁 Files Modified/Created

### To Modify:
- `message_processing.py` - Add telemetry/position logging
- `db_operations.py` - Add new query functions

### To Create:
- `mesh-observatory/` - New Flask app directory
- `telemetry_logger.py` - Standalone telemetry collector
- `mesh-observatory.service` - systemd service for dashboard
- `requirements-dashboard.txt` - Dashboard dependencies

---

## 🚀 Getting Started Commands

```bash
# Create Flask app directory
mkdir -p /home/seth/mesh-observatory
cd /home/seth/mesh-observatory

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install flask flask-socketio python-socketio plotly folium meshtastic

# Create initial files
touch app.py config.py

# Run development server
flask run --host=0.0.0.0 --port=5000
```

---

## 🔐 Security Considerations

- Add authentication (Flask-Login)
- Restrict admin routes
- Rate limiting on API
- HTTPS for production (Let's Encrypt)
- Database backups (daily cron)

---

## 📊 Data Retention Policy

- Message logs: 90 days (configurable)
- Telemetry: 30 days (hourly averages after 7 days)
- Position: 30 days
- Neighbor info: 7 days
- Admin events: Permanent

---

## 🎨 Dashboard Design Inspiration

**Color Scheme:**
- Primary: #2E7D32 (Green - mesh theme)
- Secondary: #1565C0 (Blue - tech)
- Accent: #F57C00 (Orange - alerts)
- Background: #121212 (Dark mode default)

**Widgets:**
- Mesh health gauge (green/yellow/red)
- Live activity feed (scrolling)
- Node count counter (animated)
- Signal strength bars
- Battery level icons

---

## 🔗 Integration Opportunities

### Home Assistant
- Expose MQTT topics
- Node battery sensors
- Mesh health binary_sensor
- Automation triggers

### n8n Workflows
- Webhook on new node
- Daily report generation
- Alert routing (email/SMS)
- Data export to Google Sheets

### APRS Gateway
- Bridge mesh positions to APRS
- Ham radio integration
- Emergency coordination

---

## 📈 Success Metrics

- Dashboard loads < 2 seconds
- Real-time updates < 500ms latency
- Database queries < 100ms
- 99% uptime
- Mobile-friendly (responsive design)

---

## 🐛 Known Issues to Address

- USB power issues (SOLVED: Using TCP)
- 10-minute BBS restart (Feature, not bug!)
- Message logging SNR sometimes null
- Need error handling for offline nodes

---

## 💡 Future Ideas

- Mesh network simulator (predict coverage)
- Antenna pattern analyzer
- Community leaderboard (gamification)
- Mesh radio direction finding
- Satellite pass predictor (ISS alerts)
- Integration with your "The AI Choice" book launch! (Nov 14)

---

**Ready to build! 🚀**

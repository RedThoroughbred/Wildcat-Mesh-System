"""Database operations for Mesh Observatory"""
import sqlite3
import logging
import time
from config import DATABASE_PATH

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # Return rows as dictionaries
    return conn


def initialize_observatory_tables():
    """Create new tables for observatory (doesn't touch BBS tables)"""
    conn = get_db_connection()
    c = conn.cursor()

    # Telemetry history
    c.execute('''CREATE TABLE IF NOT EXISTS telemetry_logs (
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
    )''')

    # Position history
    c.execute('''CREATE TABLE IF NOT EXISTS position_logs (
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
    )''')

    # Network topology
    c.execute('''CREATE TABLE IF NOT EXISTS neighbor_info (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER NOT NULL,
        node_id TEXT NOT NULL,
        neighbor_id TEXT NOT NULL,
        snr REAL,
        last_heard INTEGER
    )''')

    # Admin events
    c.execute('''CREATE TABLE IF NOT EXISTS admin_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        node_id TEXT,
        admin_id TEXT,
        description TEXT,
        success BOOLEAN
    )''')

    # Node metadata
    c.execute('''CREATE TABLE IF NOT EXISTS node_info (
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
    )''')

    conn.commit()
    conn.close()
    logging.info("Observatory tables initialized")


def get_active_nodes(threshold=3600):
    """Get nodes active in last N seconds"""
    import time
    conn = get_db_connection()
    c = conn.cursor()
    cutoff = int(time.time()) - threshold

    c.execute("""
        SELECT DISTINCT sender_id, sender_short_name, MAX(timestamp) as last_seen
        FROM message_logs
        WHERE timestamp >= ?
        GROUP BY sender_id
        ORDER BY last_seen DESC
    """, (cutoff,))

    nodes = c.fetchall()
    conn.close()
    return [dict(row) for row in nodes]


def get_recent_messages(limit=20):
    """Get recent messages"""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT timestamp, sender_short_name, message, snr, rssi, channel_index, to_id
        FROM message_logs
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))

    messages = c.fetchall()
    conn.close()
    return [dict(row) for row in messages]


def get_mesh_stats():
    """Get overall mesh statistics"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    # 24h message count
    cutoff_24h = int(time.time()) - 86400
    c.execute("SELECT COUNT(*) FROM message_logs WHERE timestamp >= ?", (cutoff_24h,))
    msg_count_24h = c.fetchone()[0]

    # Average SNR (last 24h)
    c.execute("SELECT AVG(snr) FROM message_logs WHERE timestamp >= ? AND snr IS NOT NULL", (cutoff_24h,))
    avg_snr = c.fetchone()[0] or 0

    # Active nodes (last hour)
    cutoff_1h = int(time.time()) - 3600
    c.execute("""
        SELECT COUNT(DISTINCT sender_id)
        FROM message_logs
        WHERE timestamp >= ?
    """, (cutoff_1h,))
    active_nodes = c.fetchone()[0]

    # Total unique nodes
    c.execute("SELECT COUNT(DISTINCT sender_id) FROM message_logs")
    total_nodes = c.fetchone()[0]

    conn.close()

    return {
        'messages_24h': msg_count_24h,
        'avg_snr': round(avg_snr, 1),
        'active_nodes': active_nodes,
        'total_nodes': total_nodes
    }


def get_channel_activity():
    """Get activity by channel"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    cutoff = int(time.time()) - 86400  # 24h
    c.execute("""
        SELECT channel_index, COUNT(*) as count
        FROM message_logs
        WHERE timestamp >= ?
        GROUP BY channel_index
        ORDER BY count DESC
    """, (cutoff,))

    channels = c.fetchall()
    conn.close()
    return [dict(row) for row in channels]


def get_top_senders(limit=10, hours=24):
    """Get most active senders"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    cutoff = int(time.time()) - (hours * 3600)

    c.execute("""
        SELECT
            sender_short_name,
            sender_id,
            COUNT(*) as message_count,
            AVG(snr) as avg_snr
        FROM message_logs
        WHERE timestamp >= ?
        GROUP BY sender_id
        ORDER BY message_count DESC
        LIMIT ?
    """, (cutoff, limit))

    senders = c.fetchall()
    conn.close()
    return [dict(row) for row in senders]


def get_channel_hourly_activity(hours=24):
    """Get message activity by hour and channel"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    cutoff = int(time.time()) - (hours * 3600)

    c.execute("""
        SELECT
            CAST(strftime('%H', datetime(timestamp, 'unixepoch', 'localtime')) AS INTEGER) as hour,
            channel_index,
            COUNT(*) as count
        FROM message_logs
        WHERE timestamp >= ? AND channel_index IS NOT NULL
        GROUP BY hour, channel_index
        ORDER BY hour, channel_index
    """, (cutoff,))

    activity = c.fetchall()
    conn.close()
    return [dict(row) for row in activity]


def get_channel_details():
    """Get detailed stats for each channel"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    cutoff = int(time.time()) - 86400

    c.execute("""
        SELECT
            channel_index,
            COUNT(*) as message_count,
            COUNT(DISTINCT sender_id) as unique_senders,
            AVG(snr) as avg_snr,
            MAX(timestamp) as last_message,
            MIN(timestamp) as first_message
        FROM message_logs
        WHERE timestamp >= ? AND channel_index IS NOT NULL
        GROUP BY channel_index
        ORDER BY message_count DESC
    """, (cutoff,))

    details = c.fetchall()
    conn.close()
    return [dict(row) for row in details]


def get_low_battery_nodes():
    """Get nodes with low battery (<20%)"""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT node_id, node_name, battery_level, timestamp
        FROM telemetry_logs
        WHERE battery_level IS NOT NULL AND battery_level < 20
        ORDER BY timestamp DESC
        LIMIT 10
    """)

    nodes = c.fetchall()
    conn.close()
    return [dict(row) for row in nodes]


def get_all_nodes_detailed():
    """Get detailed info about all nodes seen on network"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT
            m.sender_id,
            m.sender_short_name,
            n.long_name as sender_long_name,
            COUNT(*) as message_count,
            MAX(m.timestamp) as last_seen,
            MIN(m.timestamp) as first_seen,
            AVG(m.snr) as avg_snr,
            MAX(m.snr) as best_snr,
            MIN(m.snr) as worst_snr,
            AVG(m.rssi) as avg_rssi
        FROM message_logs m
        LEFT JOIN node_info n ON m.sender_id = n.node_id
        GROUP BY m.sender_id
        ORDER BY last_seen DESC
    """)

    nodes = c.fetchall()
    conn.close()
    return [dict(row) for row in nodes]


def get_node_detail(node_id):
    """Get detailed info about a specific node"""
    conn = get_db_connection()
    c = conn.cursor()

    # Basic stats
    c.execute("""
        SELECT
            sender_id,
            sender_short_name,
            COUNT(*) as message_count,
            MAX(timestamp) as last_seen,
            MIN(timestamp) as first_seen,
            AVG(snr) as avg_snr,
            MAX(snr) as best_snr,
            MIN(snr) as worst_snr,
            AVG(rssi) as avg_rssi
        FROM message_logs
        WHERE sender_id = ?
        GROUP BY sender_id
    """, (node_id,))

    node = c.fetchone()

    # Recent messages
    c.execute("""
        SELECT timestamp, message, snr, rssi, channel_index
        FROM message_logs
        WHERE sender_id = ?
        ORDER BY timestamp DESC
        LIMIT 20
    """, (node_id,))

    recent_messages = c.fetchall()

    conn.close()

    return {
        'info': dict(node) if node else None,
        'recent_messages': [dict(row) for row in recent_messages]
    }


def get_hourly_snr_trends(days=7):
    """Get average SNR by hour of day for propagation analysis"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    cutoff = int(time.time()) - (days * 86400)

    c.execute("""
        SELECT
            CAST(strftime('%H', datetime(timestamp, 'unixepoch', 'localtime')) AS INTEGER) as hour,
            AVG(snr) as avg_snr,
            AVG(rssi) as avg_rssi,
            COUNT(*) as message_count,
            COUNT(DISTINCT sender_id) as node_count
        FROM message_logs
        WHERE timestamp >= ? AND snr IS NOT NULL
        GROUP BY hour
        ORDER BY hour ASC
    """, (cutoff,))

    results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_best_worst_propagation():
    """Get best and worst propagation times/connections"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    cutoff = int(time.time()) - (7 * 86400)

    # Best connections (highest SNR)
    c.execute("""
        SELECT sender_short_name, snr, rssi, timestamp
        FROM message_logs
        WHERE timestamp >= ? AND snr IS NOT NULL
        ORDER BY snr DESC
        LIMIT 10
    """, (cutoff,))
    best = c.fetchall()

    # Worst connections (lowest SNR)
    c.execute("""
        SELECT sender_short_name, snr, rssi, timestamp
        FROM message_logs
        WHERE timestamp >= ? AND snr IS NOT NULL
        ORDER BY snr ASC
        LIMIT 10
    """, (cutoff,))
    worst = c.fetchall()

    conn.close()

    return {
        'best': [dict(row) for row in best],
        'worst': [dict(row) for row in worst]
    }


def get_snr_distribution():
    """Get SNR distribution for histogram"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    cutoff = int(time.time()) - (7 * 86400)

    c.execute("""
        SELECT snr, COUNT(*) as count
        FROM message_logs
        WHERE timestamp >= ? AND snr IS NOT NULL
        GROUP BY CAST(snr AS INTEGER)
        ORDER BY snr ASC
    """, (cutoff,))

    results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_channel_messages(channel_index, limit=500, hours=24):
    """Get messages for a specific channel (excluding direct messages)"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    cutoff = int(time.time()) - (hours * 3600)

    c.execute("""
        SELECT
            timestamp,
            sender_id,
            sender_short_name,
            message,
            snr,
            rssi
        FROM message_logs
        WHERE channel_index = ?
        AND timestamp >= ?
        AND to_id = 4294967295
        ORDER BY timestamp DESC
        LIMIT ?
    """, (channel_index, cutoff, limit))

    messages = c.fetchall()
    conn.close()
    return [dict(row) for row in messages]


def get_node_positions():
    """Get latest position for each node with GPS coordinates"""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT
            p.node_id,
            p.node_name,
            p.latitude,
            p.longitude,
            p.altitude,
            p.timestamp,
            m.sender_short_name,
            MAX(m.timestamp) as last_message_time,
            AVG(m.snr) as avg_snr
        FROM position_logs p
        LEFT JOIN message_logs m ON p.node_id = m.sender_id
        WHERE p.latitude IS NOT NULL AND p.longitude IS NOT NULL
        AND p.timestamp = (
            SELECT MAX(timestamp)
            FROM position_logs
            WHERE node_id = p.node_id
        )
        GROUP BY p.node_id
        ORDER BY p.timestamp DESC
    """)

    positions = c.fetchall()
    conn.close()
    return [dict(row) for row in positions]


def get_bbs_messages(limit=500, hours=168):
    """Get direct messages (both to and from BBS node)"""
    import time
    conn = get_db_connection()
    c = conn.cursor()

    cutoff = int(time.time()) - (hours * 3600)
    bbs_node_id = '!9e766b18'

    # Get all direct messages (not broadcasts)
    # This includes messages TO the BBS and responses FROM the BBS
    c.execute("""
        SELECT
            timestamp,
            sender_id,
            sender_short_name,
            message,
            snr,
            rssi,
            to_id
        FROM message_logs
        WHERE to_id != 4294967295
        AND to_id != '4294967295'
        AND timestamp >= ?
        ORDER BY timestamp ASC
        LIMIT ?
    """, (cutoff, limit))

    messages = []
    for row in c.fetchall():
        msg_dict = dict(row)
        # Add a flag to identify if this is from the BBS
        msg_dict['is_bbs_response'] = (msg_dict['sender_id'] == bbs_node_id)
        messages.append(msg_dict)

    conn.close()
    # Reverse so newest is at top but conversation chunks read naturally
    messages.reverse()
    return messages


def get_neighbor_info():
    """Get network topology from neighbor information"""
    conn = get_db_connection()
    c = conn.cursor()

    # Get the most recent neighbor info for each node-neighbor pair
    c.execute("""
        SELECT
            ni.node_id,
            ni.neighbor_id,
            ni.snr,
            ni.timestamp,
            n1.short_name as node_name,
            n2.short_name as neighbor_name
        FROM neighbor_info ni
        LEFT JOIN node_info n1 ON ni.node_id = n1.node_id
        LEFT JOIN node_info n2 ON ni.neighbor_id = n2.node_id
        WHERE ni.timestamp = (
            SELECT MAX(timestamp)
            FROM neighbor_info
            WHERE node_id = ni.node_id
            AND neighbor_id = ni.neighbor_id
        )
        AND ni.timestamp > ?
        ORDER BY ni.timestamp DESC
    """, (int(time.time()) - 604800,))  # Last 7 days

    neighbors = c.fetchall()
    conn.close()
    return [dict(row) for row in neighbors]

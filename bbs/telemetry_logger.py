#!/usr/bin/env python3
"""
Wildcat Mesh Telemetry Logger
Captures telemetry, position, and topology data from Meshtastic network
Runs independently alongside the BBS
"""

import logging
import time
import configparser
import sqlite3
from datetime import datetime
import meshtastic
import meshtastic.tcp_interface
import meshtastic.serial_interface

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# Database path (shared with Observatory in monorepo)
import os
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared', 'bulletins.db')


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    return conn


def log_telemetry(packet):
    """Log telemetry data (battery, voltage, temperature, etc.)"""
    try:
        if 'decoded' not in packet:
            return

        decoded = packet['decoded']
        if decoded.get('portnum') != 'TELEMETRY_APP':
            return

        telemetry = decoded.get('telemetry', {})
        device_metrics = telemetry.get('deviceMetrics', {})
        environment_metrics = telemetry.get('environmentMetrics', {})

        timestamp = packet.get('rxTime', int(time.time()))
        node_id = packet.get('fromId', 'unknown')

        conn = get_db_connection()
        c = conn.cursor()

        c.execute("""
            INSERT INTO telemetry_logs (
                timestamp, node_id, node_name, battery_level, voltage,
                channel_util, air_util_tx, temperature, humidity,
                pressure, gas_resistance, uptime_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            node_id,
            packet.get('from'),  # node_name will be updated separately
            device_metrics.get('batteryLevel'),
            device_metrics.get('voltage'),
            device_metrics.get('channelUtilization'),
            device_metrics.get('airUtilTx'),
            environment_metrics.get('temperature'),
            environment_metrics.get('relativeHumidity'),
            environment_metrics.get('barometricPressure'),
            environment_metrics.get('gasResistance'),
            device_metrics.get('uptimeSeconds')
        ))

        conn.commit()
        conn.close()

        logger.info(f"📊 Telemetry logged: {node_id} - Battery: {device_metrics.get('batteryLevel')}%")

    except Exception as e:
        logger.error(f"Error logging telemetry: {e}")


def log_position(packet):
    """Log position data (GPS coordinates, altitude, etc.)"""
    try:
        if 'decoded' not in packet:
            return

        decoded = packet['decoded']
        if decoded.get('portnum') != 'POSITION_APP':
            return

        position = decoded.get('position', {})

        timestamp = packet.get('rxTime', int(time.time()))
        node_id = packet.get('fromId', 'unknown')

        # Convert lat/lon from integer format (degrees * 1e7)
        latitude = position.get('latitude')
        longitude = position.get('longitude')

        if latitude is None or longitude is None:
            return

        # Meshtastic stores as integer, convert to float
        if isinstance(latitude, int):
            latitude = latitude / 1e7
        if isinstance(longitude, int):
            longitude = longitude / 1e7

        conn = get_db_connection()
        c = conn.cursor()

        c.execute("""
            INSERT INTO position_logs (
                timestamp, node_id, node_name, latitude, longitude,
                altitude, precision_bits, ground_speed, ground_track,
                satellites_in_view
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            node_id,
            packet.get('from'),
            latitude,
            longitude,
            position.get('altitude'),
            position.get('precisionBits'),
            position.get('groundSpeed'),
            position.get('groundTrack'),
            position.get('satsInView')
        ))

        conn.commit()
        conn.close()

        logger.info(f"📍 Position logged: {node_id} - {latitude:.4f}, {longitude:.4f}")

    except Exception as e:
        logger.error(f"Error logging position: {e}")


def log_neighbor_info(packet):
    """Log neighbor information (network topology)"""
    try:
        if 'decoded' not in packet:
            return

        decoded = packet['decoded']
        if decoded.get('portnum') != 'NEIGHBORINFO_APP':
            return

        neighbors = decoded.get('neighborinfo', {}).get('neighbors', [])

        timestamp = packet.get('rxTime', int(time.time()))
        node_id = packet.get('fromId', 'unknown')

        conn = get_db_connection()
        c = conn.cursor()

        for neighbor in neighbors:
            c.execute("""
                INSERT INTO neighbor_info (
                    timestamp, node_id, neighbor_id, snr, last_heard
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                timestamp,
                node_id,
                neighbor.get('nodeId', 'unknown'),
                neighbor.get('snr'),
                neighbor.get('lastHeard')
            ))

        conn.commit()
        conn.close()

        logger.info(f"🔗 Neighbor info logged: {node_id} - {len(neighbors)} neighbors")

    except Exception as e:
        logger.error(f"Error logging neighbor info: {e}")


def update_node_info(packet, interface):
    """Update node metadata table"""
    try:
        if 'decoded' not in packet:
            return

        decoded = packet['decoded']
        if decoded.get('portnum') != 'NODEINFO_APP':
            return

        user = decoded.get('user', {})
        node_id = packet.get('fromId', 'unknown')
        timestamp = packet.get('rxTime', int(time.time()))

        # Get hardware info from the interface if available
        node_info = interface.nodes.get(packet.get('from'), {})

        conn = get_db_connection()
        c = conn.cursor()

        c.execute("""
            INSERT INTO node_info (
                node_id, short_name, long_name, hw_model, role,
                firmware_version, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                short_name = excluded.short_name,
                long_name = excluded.long_name,
                hw_model = excluded.hw_model,
                role = excluded.role,
                firmware_version = excluded.firmware_version,
                last_seen = excluded.last_seen
        """, (
            node_id,
            user.get('shortName'),
            user.get('longName'),
            user.get('hwModel'),
            user.get('role'),
            node_info.get('deviceMetrics', {}).get('firmwareVersion'),
            timestamp,
            timestamp
        ))

        conn.commit()
        conn.close()

        logger.info(f"ℹ️ Node info updated: {user.get('shortName')} ({node_id})")

    except Exception as e:
        logger.error(f"Error updating node info: {e}")


def on_receive(packet, interface):
    """Main packet handler - routes to appropriate logger"""
    try:
        # Log different packet types
        log_telemetry(packet)
        log_position(packet)
        log_neighbor_info(packet)
        update_node_info(packet, interface)

    except Exception as e:
        logger.error(f"Error processing packet: {e}")


def main():
    """Main telemetry logger loop"""
    logger.info("=" * 50)
    logger.info("🛰️  Wildcat Mesh Telemetry Logger")
    logger.info("=" * 50)

    # Load config
    config = configparser.ConfigParser()
    config.read('/home/seth/Wildcat-TC2-BBS/config.ini')

    interface_type = config.get('interface', 'type', fallback='serial')

    try:
        # Connect to Meshtastic interface
        if interface_type == 'tcp':
            hostname = config.get('interface', 'hostname')
            logger.info(f"Connecting to Meshtastic via TCP: {hostname}")
            interface = meshtastic.tcp_interface.TCPInterface(hostname=hostname)
        else:
            logger.info("Connecting to Meshtastic via Serial")
            interface = meshtastic.serial_interface.SerialInterface()

        logger.info("✅ Connected to Meshtastic!")
        logger.info("📊 Logging telemetry data...")
        logger.info("📍 Logging position data...")
        logger.info("🔗 Logging neighbor info...")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 50)

        # Subscribe to all messages
        pub.subscribe(lambda packet, interface=interface: on_receive(packet, interface), "meshtastic.receive")

        # Keep running
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("\n👋 Shutting down telemetry logger...")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise


if __name__ == '__main__':
    # Import pubsub here to avoid issues
    from pubsub import pub
    main()

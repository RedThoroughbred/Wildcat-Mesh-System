#!/usr/bin/env python3
"""
Wildcat Mesh Telemetry Logger
Captures telemetry, position, and topology data from Meshtastic network
Runs independently alongside the BBS
"""

import argparse
import logging
import sys
import threading
import time
import sqlite3
from datetime import datetime

import _bootstrap  # noqa: F401  (repo root on sys.path so `wildcat` imports from any CWD)
from config_init import initialize_config, get_interface, ConfigError
from wildcat.config import get_config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def get_db_connection():
    """Get database connection — path + pragmas from [database] in wildcat.toml."""
    db = get_config().database
    conn = sqlite3.connect(str(db.path), timeout=db.busy_timeout_ms / 1000)
    conn.execute(f"PRAGMA busy_timeout = {int(db.busy_timeout_ms)}")
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

    # The same validated config the BBS uses — no more hardcoded INI path
    # (the old one pointed at /home/seth/Wildcat-TC2-BBS, which no longer exists,
    # so this logger silently fell back to a serial port it didn't have).
    parser = argparse.ArgumentParser(description="Wildcat Mesh Telemetry Logger")
    parser.add_argument("--config", "-c", default=None,
                        help="config file (default: $WILDCAT_CONFIG or <repo>/config/wildcat.toml)")
    args = parser.parse_args()
    try:
        system_config = initialize_config(args.config)
    except ConfigError as e:
        logger.error("CONFIG ERROR\n%s", e)
        sys.exit(2)

    cfg = system_config['wildcat']
    if cfg.telemetry.source == "bus":
        # v2: meshd owns the radio and republishes every packet; we subscribe and feed
        # the same log_* functions the original packet dict.
        from wildcat.bus import make_bus
        from wildcat.busiface import BusInterface
        bus = make_bus(cfg.mqtt, client_id="wildcat-telemetry")
        bus.start()
        if not bus.wait_connected(30):
            logger.error("Cannot reach the MQTT broker at %s:%s — is mosquitto running?", cfg.mqtt.host, cfg.mqtt.port)
            sys.exit(3)
        iface = BusInterface(bus, cfg, client_name="telemetry")
        logger.info("Waiting for meshd (the radio owner) to report a connected node…")
        if not iface.wait_ready(120):
            logger.error("meshd never reported a connected radio — is wildcat-meshd running?")
            sys.exit(3)
        iface.start(on_receive)
        logger.info("✅ Logging telemetry/position/neighbor/node info from the bus (meshd owns the radio)")
        try:
            while True:
                time.sleep(1)       # paho reconnects the bus by itself; meshd reconnects the radio
        except KeyboardInterrupt:
            logger.info("\n👋 Shutting down telemetry logger...")
        return

    try:
        # Connect to Meshtastic interface (shared with the BBS: serial auto-detect, tcp host)
        logger.info(f"Connecting to Meshtastic via {system_config['interface_type']}"
                    + (f": {system_config['hostname']}" if system_config['hostname'] else ""))
        interface = get_interface(system_config)

        logger.info("✅ Connected to Meshtastic!")
        logger.info("📊 Logging telemetry data...")
        logger.info("📍 Logging position data...")
        logger.info("🔗 Logging neighbor info...")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 50)

        # Subscribe to all messages
        pub.subscribe(lambda packet, interface=interface: on_receive(packet, interface), "meshtastic.receive")

        # Exit 3 when the node drops us, so systemd's Restart=on-failure reconnects
        # (otherwise this loop would idle forever on a dead socket).
        lost = threading.Event()

        def on_connection_lost(interface=None, **kwargs):
            logger.error("Radio connection lost — exiting so the service manager restarts us")
            lost.set()

        pub.subscribe(on_connection_lost, "meshtastic.connection.lost")

        # Keep running
        while not lost.is_set():
            time.sleep(1)
        sys.exit(3)

    except KeyboardInterrupt:
        logger.info("\n👋 Shutting down telemetry logger...")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise


if __name__ == '__main__':
    # Import pubsub here to avoid issues
    from pubsub import pub
    main()

#!/usr/bin/env python3

"""
TC²-BBS Server for Meshtastic by TheCommsChannel (TC²)
Date: 07/14/2024
Version: 0.1.6

Description:
The system allows for mail message handling, bulletin boards, and a channel
directory. It uses a configuration file for setup details and an SQLite3
database for data storage. Mail messages and bulletins are synced with
other BBS servers listed in the config.ini file.
"""

import logging
import sys
import threading
import time

import _bootstrap  # noqa: F401  (repo root on sys.path so `wildcat` imports from any CWD)
from config_init import initialize_config, get_interface, init_cli_parser, merge_config, ConfigError
from db_operations import initialize_database
from js8call_integration import JS8CallClient
from message_processing import on_receive
from pubsub import pub

# General logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# JS8Call logging
js8call_logger = logging.getLogger('js8call')
js8call_logger.setLevel(logging.DEBUG)
js8call_handler = logging.StreamHandler()
js8call_handler.setLevel(logging.DEBUG)
js8call_formatter = logging.Formatter('%(asctime)s - JS8Call - %(levelname)s - %(message)s', '%Y-%m-%d %H:%M:%S')
js8call_handler.setFormatter(js8call_formatter)
js8call_logger.addHandler(js8call_handler)

def display_banner():
    banner = """
████████╗ ██████╗██████╗       ██████╗ ██████╗ ███████╗
╚══██╔══╝██╔════╝╚════██╗      ██╔══██╗██╔══██╗██╔════╝
   ██║   ██║      █████╔╝█████╗██████╔╝██████╔╝███████╗
   ██║   ██║     ██╔═══╝ ╚════╝██╔══██╗██╔══██╗╚════██║
   ██║   ╚██████╗███████╗      ██████╔╝██████╔╝███████║
   ╚═╝    ╚═════╝╚══════╝      ╚═════╝ ╚═════╝ ╚══════╝
Meshtastic Version
"""
    print(banner)

def main():
    display_banner()
    args = init_cli_parser()
    config_file = None
    if args.config is not None:
        config_file = args.config
    try:
        system_config = initialize_config(config_file)
    except ConfigError as e:
        # Fail at boot with the [table].key that's wrong — never a KeyError mid-DM.
        logging.error("CONFIG ERROR\n%s", e)
        sys.exit(2)

    merge_config(system_config, args)

    cfg = system_config['wildcat']
    initialize_database()
    lost = threading.Event()

    if cfg.bbs.source == "bus":
        # v2: meshd owns the radio; we talk to it over MQTT through an adapter that
        # looks like the meshtastic interface (wildcat/busiface.py), so every
        # handler below runs unchanged.
        from wildcat.bus import make_bus
        from wildcat.busiface import BusInterface
        bus = make_bus(cfg.mqtt, client_id="wildcat-bbs")
        bus.start()
        if not bus.wait_connected(30):
            logging.error("Cannot reach the MQTT broker at %s:%s — is mosquitto running?", cfg.mqtt.host, cfg.mqtt.port)
            sys.exit(3)
        interface = BusInterface(bus, cfg, client_name="bbs")
        interface.bbs_nodes = system_config['bbs_nodes']
        interface.allowed_nodes = system_config['allowed_nodes']
        logging.info("Waiting for meshd (the radio owner) to report a connected node…")
        if not interface.wait_ready(120):
            logging.error("meshd never reported a connected radio — is wildcat-meshd running? "
                          "(journalctl -u wildcat-meshd)")
            sys.exit(3)
        interface.start(lambda packet, iface: on_receive(packet, iface))
        logging.info(f"TC²-BBS is running on the MQTT bus (node {interface.myInfo.my_node_num}; meshd owns the radio)...")
    else:
        interface = get_interface(system_config)
        interface.bbs_nodes = system_config['bbs_nodes']
        interface.allowed_nodes = system_config['allowed_nodes']

        logging.info(f"TC²-BBS is running on {system_config['interface_type']} interface...")

        def receive_packet(packet, interface):
            on_receive(packet, interface)

        pub.subscribe(receive_packet, system_config['mqtt_topic'])

        # If the node reboots / drops the TCP session, the meshtastic reader thread ends
        # and publishes connection.lost — but this process would otherwise sit here
        # forever with a dead socket. Exit 3 so systemd (Restart=on-failure) brings us
        # back; the legacy unit's RuntimeMaxSec self-kill is no longer needed.
        def on_connection_lost(interface=None, **kwargs):
            logging.error("Radio connection lost — exiting so the service manager restarts us")
            lost.set()

        pub.subscribe(on_connection_lost, "meshtastic.connection.lost")

    # Initialize and start JS8Call Client if configured
    js8call_client = JS8CallClient(interface)
    js8call_client.logger = js8call_logger

    if js8call_client.db_conn:
        js8call_client.connect()

    try:
        while not lost.is_set():
            time.sleep(1)

    except KeyboardInterrupt:
        logging.info("Shutting down the server...")
        interface.close()
        if js8call_client.connected:
            js8call_client.close()
        return

    try:
        interface.close()
    except Exception:
        pass
    sys.exit(3)

if __name__ == "__main__":
    main()

import time
from typing import Any

import _bootstrap  # noqa: F401  (makes `wildcat` importable from any CWD)
from wildcat.config import ConfigError, load as wildcat_load  # noqa: F401 (ConfigError re-exported for callers)
import meshtastic.stream_interface
import meshtastic.serial_interface
import meshtastic.tcp_interface
import serial.tools.list_ports
import argparse


def init_cli_parser() -> argparse.Namespace:
    """Function build the CLI parser and parses the arguments.

    Returns:
        argparse.ArgumentParser: Argparse namespace with processed CLI args
    """
    parser = argparse.ArgumentParser(description="Meshtastic BBS system")
    
    parser.add_argument(
        "--config", "-c",
        action="store",
        help="System configuration file",
        default=None)
    
    parser.add_argument(
        "--interface-type", "-i",
        action="store",
        choices=['serial', 'tcp'],
        help="Node interface type",
        default=None)
    
    parser.add_argument(
        "--port", "-p",
        action="store",
        help="Serial port",
        default=None)
    
    parser.add_argument(
        "--host", 
        action="store",
        help="TCP host address",
        default=None)
    
    parser.add_argument(
        "--mqtt-topic", '-t', 
        action="store",
        help="MQTT topic to subscribe",
        default='meshtastic.receive')
    #
    # Add extra arguments here
    #...
    
    args = parser.parse_args()
    
    return args
    
    
def merge_config(system_config:dict[str, Any], args:argparse.Namespace) -> dict[str, Any]:
    """Function merges configuration read from the config file and provided on the CLI.
    
    CLI arguments override values defined in the config file.
    system_config argument is mutated by the function.

    Args:
        system_config (dict[str, Any]): System config dict returned by initialize_config()
        args (argparse.Namespace): argparse namespace with parsed CLI args

    Returns:
        dict[str, Any]: system config dict with merged configurations
    """
    
    if args.interface_type is not None:
        system_config['interface_type'] = args.interface_type
        
    if args.port is not None:
        system_config['port'] = args.port
        
    if args.host is not None:
        system_config['hostname'] = args.host
    
    return system_config


def initialize_config(config_file: str = None) -> dict[str, Any]:
    """
    Load the ONE validated config (config/wildcat.toml, or the legacy bbs/config.ini
    through the migrator) and return the dict shape the rest of the BBS expects.

    Resolution (see wildcat/paths.py): explicit path → $WILDCAT_CONFIG →
    <repo>/config/wildcat.toml → /etc/wildcat/wildcat.toml → <repo>/bbs/config.ini.
    Nothing depends on the current working directory, a missing file lists every
    path tried, and a bad value names the [table].key — no more silent empty reads
    or KeyErrors deep in a handler (docs/DECISIONS.md D-001..D-004).

    Raises wildcat.config.ConfigError — callers print it and exit 2.

    Returns a dict with:
    wildcat - the typed WildcatConfig
    config - (compat) the same object; v1 callers held a ConfigParser here
    interface_type / hostname / port / bbs_nodes / allowed_nodes / mqtt_topic
    """
    cfg = wildcat_load(config_file)

    print(f"Config: {cfg.source} [{cfg.source_kind}]")
    print(f"Configured to sync with the following BBS nodes: {cfg.bbs.sync_nodes}")
    print(f"Nodes with Urgent board permissions: {cfg.bbs.allowed_nodes}")

    return {
        'wildcat': cfg,
        'config': cfg,
        'interface_type': cfg.radio.type,
        'hostname': cfg.radio.host,
        'port': cfg.radio.port,
        'bbs_nodes': list(cfg.bbs.sync_nodes),
        'allowed_nodes': list(cfg.bbs.allowed_nodes),
        'mqtt_topic': 'meshtastic.receive'
    }



def get_interface(system_config:dict[str, Any]) -> meshtastic.stream_interface.StreamInterface:
    """
    Function opens and returns an instance meshtastic interface of type specified by the configuration
    
    Function creates and returns an instance of a class inheriting from meshtastic.stream_interface.StreamInterface.
    The type of the class depends on the type of the interface specified by the system configuration.
    For 'serial' interfaces, function returns an instance of meshtastic.serial_interface.SerialInterface,
    and for 'tcp' interface, an instance of meshtastic.tcp_interface.TCPInterface.

    Args:
        system_config (dict[str, Any]): A dict with system configuration. See description of initialize_config() for details.

    Raises:
        ValueError: Exception raised in the following cases:
                - Type of interface not provided in the system config
                - Multiple serial ports present in the system, and no port specified in the configuration
                - Serial port interface requested, but no ports found in the system
                - Hostname not provided for TCP interface

    Returns:
        meshtastic.stream_interface.StreamInterface: An instance of StreamInterface
    """
    while True:
        try:
            if system_config['interface_type'] == 'serial':
                if system_config['port']:
                    return meshtastic.serial_interface.SerialInterface(system_config['port'])
                else:
                    ports = list(serial.tools.list_ports.comports())
                    if len(ports) == 1:
                        return meshtastic.serial_interface.SerialInterface(ports[0].device)
                    elif len(ports) > 1:
                        port_list = ', '.join([p.device for p in ports])
                        raise ValueError(f"Multiple serial ports detected: {port_list}. Specify one with the 'port' argument.")
                    else:
                        raise ValueError("No serial ports detected.")
            elif system_config['interface_type'] == 'tcp':
                if not system_config['hostname']:
                    raise ValueError("Hostname must be specified for TCP interface")
                return meshtastic.tcp_interface.TCPInterface(hostname=system_config['hostname'])
            else:
                raise ValueError("Invalid interface type specified in config file")
        except PermissionError as e:
            print(f"PermissionError: {e}. Retrying in 5 seconds...")
            time.sleep(5)

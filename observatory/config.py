"""Configuration for Mesh Observatory Dashboard.

v2: this is a thin shim over the ONE validated config (``config/wildcat.toml``,
see ``wildcat/config.py`` and docs/DECISIONS.md D-001). The module-level
constants below are kept so ``app.config.from_object(config)`` and every
``config.X`` reference in app.py / modules / templates keep working unchanged.
Nothing is hardcoded here anymore — edit wildcat.toml instead.
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:                       # repo root, derived from this file — never the CWD
    sys.path.insert(0, _ROOT)

from wildcat.config import ConfigError, get_config  # noqa: E402

try:
    _cfg = get_config()
except ConfigError as e:
    print(f"CONFIG ERROR\n{e}", file=sys.stderr)
    raise SystemExit(2)

CONFIG_PATH = str(_cfg.source)
CONFIG_KIND = _cfg.source_kind                  # "toml" | "legacy-ini"

# Database (shared with BBS + telemetry)
DATABASE_PATH = str(_cfg.database.path)
DB_BUSY_TIMEOUT_MS = _cfg.database.busy_timeout_ms

# Meshtastic connection (informational — the Observatory never opens the radio itself)
MESH_INTERFACE_TYPE = _cfg.radio.type
MESH_HOSTNAME = _cfg.radio.host

# Flask settings
SECRET_KEY = _cfg.observatory.secret_key
DEBUG = _cfg.observatory.debug                  # default False — see DECISIONS.md D-006
HOST = _cfg.observatory.host
PORT = _cfg.observatory.port

# Dashboard settings
REFRESH_INTERVAL = _cfg.observatory.refresh_interval
MAX_RECENT_MESSAGES = _cfg.observatory.max_recent_messages
ACTIVE_NODE_THRESHOLD = _cfg.observatory.active_node_threshold
BBS_NODE_ID = _cfg.observatory.bbs_node_id

# Where the BBS keeps fortunes.txt / trivia.txt / messages.json (admin editor)
BBS_CONTENT_DIR = str(_cfg.bbs.content_dir)

# Theme colors (dark mode)
THEME = {
    'primary': '#2E7D32',      # Green - mesh theme
    'secondary': '#1565C0',    # Blue - tech
    'accent': '#F57C00',       # Orange - alerts
    'background': '#121212',   # Dark background
    'surface': '#1E1E1E',      # Card background
    'text_primary': '#FFFFFF',
    'text_secondary': '#B0B0B0',
    'success': '#4CAF50',
    'warning': '#FF9800',
    'danger': '#F44336'
}

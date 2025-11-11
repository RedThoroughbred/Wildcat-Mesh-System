"""Configuration for Mesh Observatory Dashboard"""
import os

# Database path (shared with BBS in monorepo)
DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared', 'bulletins.db')

# Meshtastic connection (same as BBS config)
MESH_INTERFACE_TYPE = 'tcp'
MESH_HOSTNAME = '192.168.86.37'

# Flask settings
SECRET_KEY = 'wildcat-mesh-observatory-2025'
DEBUG = True
HOST = '0.0.0.0'
PORT = 5001

# Dashboard settings
REFRESH_INTERVAL = 5  # seconds
MAX_RECENT_MESSAGES = 20
ACTIVE_NODE_THRESHOLD = 3600  # 1 hour in seconds

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

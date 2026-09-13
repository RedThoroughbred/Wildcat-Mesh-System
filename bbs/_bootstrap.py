"""Make the ``wildcat`` package importable when the repo isn't pip-installed.

``python bbs/server.py`` puts ``bbs/`` on ``sys.path`` (the script's own dir),
not the repo root — so ``import wildcat`` would fail unless the venv has
``pip install -e .``. This shim adds the repo root, derived from THIS file's
location (never the CWD). Import it before any ``wildcat`` import.
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

"""``python -m wildcat …`` == ``wildcat …``."""
import sys

from .cli import main

sys.exit(main())

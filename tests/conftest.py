# -*- coding: utf-8 -*-
"""Test fixtures and path configuration."""

import sys
from pathlib import Path

# Add plugin directory and parent plugins directory to sys.path
plugin_dir = Path(__file__).parent.parent.resolve()
if str(plugin_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dir))

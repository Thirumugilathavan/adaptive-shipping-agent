"""
Adaptive Shipping Intelligence Agent — Main Streamlit Application Entrypoint.
Delegates to src/ui/app.py with project root explicitly added to sys.path.
Compatible with Streamlit Community Cloud and local execution.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Guarantee project root is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Import main UI entrypoint from src.ui.app
from src.ui.app import main

if __name__ == "__main__":
    main()

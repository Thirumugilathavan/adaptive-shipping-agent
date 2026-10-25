"""
Vercel Serverless Function entry point for Adaptive Shipping Intelligence Agent.
Exposes the FastAPI ASGI application instance for Vercel's Python runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path so 'src' and modules resolve properly
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.server import app

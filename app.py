"""
Adaptive Shipping Intelligence Agent — Main Web Server Entrypoint.
Launches the modern HTML, CSS & JavaScript application powered by FastAPI and Uvicorn.

Usage:
    python app.py
    Open browser at: http://localhost:8000
"""

from __future__ import annotations

import os
import sys
import webbrowser
import threading
from pathlib import Path
import uvicorn

# Guarantee project root is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def open_browser(url: str, delay_seconds: float = 1.0) -> None:
    """Helper to open the default browser once server starts."""
    import time
    time.sleep(delay_seconds)
    webbrowser.open(url)


def main():
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    url = f"http://{host}:{port}"

    print("=" * 72)
    print("🚀 Adaptive Shipping Intelligence Agent (HTML / CSS / JavaScript)")
    print(f"📡 Web Application URL: {url}")
    print("✨ Features: Rate Shopping, 10-Scenario Evaluator, 3D Packaging,")
    print("            Human Approval Gate, AWB Generation & Label Download")
    print("=" * 72)

    # Launch browser automatically in a background daemon thread
    if os.getenv("NO_BROWSER", "0") != "1":
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    # Start Uvicorn ASGI Server
    uvicorn.run("src.server:app", host=host, port=port, log_level="info", reload=False)


if __name__ == "__main__":
    main()

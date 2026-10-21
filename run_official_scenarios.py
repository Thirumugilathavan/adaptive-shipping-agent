#!/usr/bin/env python
"""
Standalone CLI Runner for Official Hackathon Test Cases (TC1 – TC4).
Executes internal verification scenarios and records outcomes to SQLite.
Usage:
    python run_official_scenarios.py
"""

from src.domain.official_scenarios import main

if __name__ == "__main__":
    main()

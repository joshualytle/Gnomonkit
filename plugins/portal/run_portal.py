#!/usr/bin/env python3
"""Gnomonkit portal entry point: python run_portal.py serve | refresh | token | login-url | status

A script rather than `python -m`, so a scheduled task or service unit can start it from any working
directory with nothing on PYTHONPATH.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gnomonkit_portal.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

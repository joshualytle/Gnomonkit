#!/usr/bin/env python3
"""Launcher for the supply-chain review. Run it with `python -I` so nothing from the current
folder or the environment can be imported in its place:

    python -I path/to/plugins/supply-chain/supply_review.py release npm left-pad 1.2.0..1.3.0
    python -I path/to/plugins/supply-chain/supply_review.py patch --base origin/main --head HEAD

Isolated mode leaves the script's own folder off sys.path, so it is added back explicitly here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supply_chain.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

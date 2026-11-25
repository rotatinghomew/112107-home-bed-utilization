#!/usr/bin/env python3
"""Convenience entry point: ``python3 main.py ...``."""

import sys
from pathlib import Path

# Ensure the project root is importable even under ``python3 -I``.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bed_accounting.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

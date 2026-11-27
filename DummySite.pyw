"""Launcher without a console window. Double-click this file, or run: pythonw DummySite.pyw"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.app import main  # noqa: E402

if __name__ == "__main__":
    main()

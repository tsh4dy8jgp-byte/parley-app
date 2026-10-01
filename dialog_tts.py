"""Launcher: `python dialog_tts.py render lektion_02.tagged.txt -o out/` without installing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from parley.dialog_tts.cli import main  # noqa: E402

if __name__ == "__main__":
    main()

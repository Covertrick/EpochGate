import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT, ROOT.parent / "scripts"):
    text = str(entry)
    if text not in sys.path:
        sys.path.insert(0, text)

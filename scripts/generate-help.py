"""Regenerate the public guide from the same content used by offline native help."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root/'src'))
from frw.support import guide_markdown

destination = root/'docs/USER-GUIDE.md'
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(guide_markdown(), encoding='utf-8')
print('Updated docs/USER-GUIDE.md')

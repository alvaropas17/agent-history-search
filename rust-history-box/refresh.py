"""Refresh the existing derived index; original stores remain read-only."""
import sys
from pathlib import Path

scripts = Path.home() / ".agents" / "skills" / "shared-scripts" / "scripts"
sys.path.insert(0, str(scripts))
import history_index

history_index.update(list(history_index.HOSTS))

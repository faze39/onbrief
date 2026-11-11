import sys
from pathlib import Path

# Import the plugin as package "onbrief" from the repo root's parent.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
if ROOT.name != "onbrief":
    raise RuntimeError("run tests from a checkout directory named 'onbrief'")

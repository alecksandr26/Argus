import sys
from pathlib import Path

# Allow `pytest` from src/dataset/ without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

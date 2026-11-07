"""Make ``backend/`` importable for ``python -m scripts.<name>`` run from the repo root."""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

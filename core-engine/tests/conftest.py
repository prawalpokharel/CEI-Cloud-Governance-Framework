import sys
from pathlib import Path

# Tests import both `src.*` (the application) and `tests.*` (the golden spec),
# so the core-engine directory must be on the path regardless of where pytest
# was invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

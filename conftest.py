"""Make `src` and the fixture generator importable from tests."""

import os
import sys

ROOT = os.path.dirname(__file__)
for _path in (ROOT, os.path.join(ROOT, "data", "synthetic")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

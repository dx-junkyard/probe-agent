import os
import sys

# Make `app` package importable when running pytest from repo root or from
# apps/control-server.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

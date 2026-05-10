#!/opt/jupyter-env/bin/python3
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portal.app import create_app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8091"))
    app = create_app(ROOT)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

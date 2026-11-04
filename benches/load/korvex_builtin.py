"""The Korvex load-test app (korvex_app.py), served by Korvex's built-in
server instead of uvicorn. Run: python benches/load/korvex_builtin.py"""

import os

from korvex_app import app

app.run(port=int(os.environ.get("PORT", "8000")))

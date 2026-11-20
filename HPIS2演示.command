#!/bin/bash
# Double-click on macOS (or run on Linux) to start the HPIS 2.0 demo and open the browser.
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv-demo/bin/python ]; then
  python3 -m venv .venv-demo
  .venv-demo/bin/python -m pip install --upgrade pip >/dev/null
  .venv-demo/bin/python -m pip install -e . -r demo/requirements.txt
fi
exec .venv-demo/bin/python -m demo --open "$@"

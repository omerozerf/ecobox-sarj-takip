#!/bin/bash
# Creates the project-local .venv (for the charts) and installs requirements.txt into it.
# Run again after changing requirements.txt; then re-run install_launchd.sh so the service
# picks up the .venv interpreter.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASE_PYTHON="${BASE_PYTHON:-/opt/homebrew/bin/python3}"

if [[ ! -x "$BASE_PYTHON" ]]; then
    echo "$BASE_PYTHON bulunamadı (BASE_PYTHON ile başka bir python3 verilebilir)." >&2
    exit 1
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/python3" ]]; then
    "$BASE_PYTHON" -m venv "$PROJECT_DIR/.venv"
fi
"$PROJECT_DIR/.venv/bin/python3" -m pip install --quiet --upgrade pip
"$PROJECT_DIR/.venv/bin/python3" -m pip install --quiet -r "$PROJECT_DIR/requirements.txt"
"$PROJECT_DIR/.venv/bin/python3" -c "import matplotlib; print('matplotlib', matplotlib.__version__)"
echo "Hazır: $PROJECT_DIR/.venv"

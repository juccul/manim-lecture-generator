#!/usr/bin/env bash
set -euo pipefail

python_bin="${PYTHON:-python3.12}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin_dir="$(cd "$script_dir/.." && pwd)"

if [ ! -d ".venv" ]; then
  "$python_bin" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r "$plugin_dir/requirements.txt"

echo "Setup complete. Use .venv/bin/python for lecture generation."

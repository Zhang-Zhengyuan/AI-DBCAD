#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HERE/.local/venv/bin:$HERE/../neuralcad_edit/.local/bin:$PATH"
export PYTHONPATH="$HERE/.local/upstream:$HERE${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME="$HERE/../neuralcad_edit/.local/cache/huggingface"
export MPLCONFIGDIR="$HERE/.local/cache/matplotlib"
export MPLBACKEND=Agg
cd "$HERE"
exec "$HERE/.local/venv/bin/python" "$HERE/study.py" "$@"

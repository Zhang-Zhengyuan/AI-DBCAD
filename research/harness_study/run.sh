#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
AGENTIC="$HERE/../agenticcad_edit"
export PATH="$AGENTIC/.local/venv/bin:$HERE/../neuralcad_edit/.local/bin:$PATH"
export PYTHONPATH="$AGENTIC/.local/upstream:$AGENTIC:$HERE${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME="$HERE/../neuralcad_edit/.local/cache/huggingface"
export MPLCONFIGDIR="$HERE/.local/cache/matplotlib"
export MPLBACKEND=Agg
export PYTHONUNBUFFERED=1
cd "$HERE"
ENTRY="$HERE/experiment.py"
if [[ "${1:-}" == "evaluate" ]]; then ENTRY="$HERE/evaluate.py"; shift; fi
if [[ "${1:-}" == "observation" ]]; then ENTRY="$HERE/observation_probe.py"; shift; fi
if [[ "${1:-}" == "summarize" ]]; then ENTRY="$HERE/summarize.py"; shift; fi
exec "$AGENTIC/.local/venv/bin/python" "$ENTRY" "$@"

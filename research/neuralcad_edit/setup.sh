#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
mkdir -p .local
COMMIT=68d1acceda0743e364f45fb7823f7cf39c530ba5
if [[ ! -d .local/upstream/.git ]]; then
    git clone https://github.com/AutodeskAILab/neuralCAD-Edit.git .local/upstream
fi
if [[ -n "$(git -C .local/upstream status --porcelain)" ]]; then
    echo 'Official checkout has local changes; preserve them before setup.' >&2
    exit 1
fi
git -C .local/upstream checkout --detach "$COMMIT"
UV_PROJECT_ENVIRONMENT=.local/venv uv sync --frozen --python 3.12
./.local/venv/bin/python -m ipykernel install --prefix "$HERE/.local/venv" --name neuralcad-edit --display-name 'neuralCAD-Edit ARM64'
./.local/venv/bin/python data.py --extract
./run.sh weights
./run.sh configure
./run.sh doctor
./run.sh catalog
echo 'Ready. Run ./run.sh smoke, then ./run.sh leaderboard.'

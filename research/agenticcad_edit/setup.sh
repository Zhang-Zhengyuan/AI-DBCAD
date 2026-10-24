#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
mkdir -p .local
COMMIT=c084034cdcf462025a60ee70c5ba94577428e5ca
if [[ ! -d .local/upstream/.git ]]; then
    git clone --recurse-submodules https://github.com/agenticCADedit/agenticCADedit.git .local/upstream
fi
if [[ -n "$(git -C .local/upstream status --porcelain)" ]]; then
    echo 'Official checkout has local changes; preserve them before setup.' >&2
    exit 1
fi
git -C .local/upstream checkout --detach "$COMMIT"
UV_PROJECT_ENVIRONMENT=.local/venv uv sync --frozen --python 3.12
if [[ ! -f .local/pricing.json ]]; then
    cp pricing.example.json .local/pricing.json
fi
./run.sh doctor
echo 'Ready. Run ./run.sh smoke and ./run.sh published; API runs require local pricing and --execute.'

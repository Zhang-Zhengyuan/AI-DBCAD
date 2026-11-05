#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/.docker/bin:$HOME/.local/share/fnm/aliases/default/bin:$PATH"
cd "$(dirname "$0")"
python3 scripts/macos.py start --open

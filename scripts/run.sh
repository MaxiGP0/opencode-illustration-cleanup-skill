#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$SKILL_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    bash "$SKILL_DIR/scripts/bootstrap.sh"
fi

export U2NET_HOME="$SKILL_DIR/models/rembg"
export TORCH_HOME="$SKILL_DIR/models/torch"
export HF_HOME="$SKILL_DIR/models/hf"
export PYTHONUTF8=1

exec "$PYTHON" "$SKILL_DIR/scripts/pipeline.py" "$@"

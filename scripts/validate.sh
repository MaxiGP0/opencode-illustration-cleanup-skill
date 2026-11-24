#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$SKILL_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    bash "$SKILL_DIR/scripts/bootstrap.sh"
fi

exec "$PYTHON" "$SKILL_DIR/scripts/validate_output.py" "$@"

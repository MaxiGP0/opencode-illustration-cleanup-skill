#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="${OPENCODE_SKILLS_DIR:-$HOME/.config/opencode/skills}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)
            SKILL_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: verify.sh [--dir PATH]"
            exit 0
            ;;
        *)
            echo "Opción desconocida: $1" >&2
            exit 1
            ;;
    esac
done

TARGET="$SKILL_DIR/illustration-cleanup"
ERRORS=0

check_file() {
    if [ ! -f "$TARGET/$1" ]; then
        echo "Falta: $1" >&2
        ERRORS=$((ERRORS + 1))
    fi
}

check_file "SKILL.md"
check_file "requirements.txt"
check_file "LICENSE"
check_file "NOTICE"
check_file "scripts/pipeline.py"
check_file "scripts/run.ps1"
check_file "scripts/run.sh"
check_file "scripts/bootstrap.ps1"
check_file "scripts/bootstrap.sh"
check_file "scripts/validate_output.py"

if [ -f "$TARGET/SKILL.md" ]; then
    if ! grep -Eq '^name:[[:space:]]*illustration-cleanup[[:space:]]*$' "$TARGET/SKILL.md"; then
        echo "SKILL.md no declara name: illustration-cleanup" >&2
        ERRORS=$((ERRORS + 1))
    fi
    if ! grep -Eq '^description:[[:space:]]*[^[:space:]]' "$TARGET/SKILL.md"; then
        echo "SKILL.md no tiene description" >&2
        ERRORS=$((ERRORS + 1))
    fi
fi

if [ "$ERRORS" -gt 0 ]; then
    echo "Verificación fallida en $TARGET" >&2
    exit 1
fi

echo "OK: $TARGET"
echo "Archivos requeridos presentes; SKILL.md válido."

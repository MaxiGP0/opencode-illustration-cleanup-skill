#!/usr/bin/env bash
set -euo pipefail

SKILL_NAME="illustration-cleanup"
SKILL_DIR="${OPENCODE_SKILLS_DIR:-$HOME/.config/opencode/skills}"
REF="main"
FORCE=0

usage() {
    cat <<'EOF'
Usage: install.sh [--dir PATH] [--ref REF] [--force]

  --dir PATH   OpenCode skills root (default: ~/.config/opencode/skills)
  --ref REF    Branch, tag, or commit (default: main)
  --force      Replace an existing installation
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)
            SKILL_DIR="$2"
            shift 2
            ;;
        --ref)
            REF="$2"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Opción desconocida: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

TARGET="$SKILL_DIR/$SKILL_NAME"
TMP=""

cleanup() {
    if [ -n "$TMP" ]; then
        rm -rf "$TMP"
    fi
}
trap cleanup EXIT

if [ -e "$TARGET" ]; then
    if [ "$FORCE" -ne 1 ]; then
        echo "Ya existe $TARGET. Usá --force para reemplazarlo." >&2
        exit 1
    fi
    rm -rf "$TARGET"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
if [ -f "$SCRIPT_DIR/SKILL.md" ]; then
    SOURCE="$SCRIPT_DIR"
else
    TMP="$(mktemp -d)"
    URL="https://codeload.github.com/MaxiGP0/opencode-illustration-cleanup-skill/tar.gz/$REF"
    echo "Descargando $URL"
    curl -fsSL "$URL" | tar -xz -C "$TMP"
    SOURCE="$(find "$TMP" -maxdepth 2 -name SKILL.md -print -quit | xargs dirname)"
fi

mkdir -p "$TARGET"
cp "$SOURCE/SKILL.md" "$SOURCE/requirements.txt" "$SOURCE/LICENSE" "$SOURCE/NOTICE" "$TARGET/"
cp -R "$SOURCE/scripts" "$TARGET/"
rm -rf "$TARGET/scripts/__pycache__"

echo ""
echo "Instalado en: $TARGET"
echo "Primera ejecución:"
echo "  $TARGET/scripts/run.sh --help"
echo ""
echo "La primera corrida descarga Python, dependencias y modelos (~2 GB)."

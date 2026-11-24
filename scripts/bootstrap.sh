#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$SKILL_DIR/tools"
UV_LOCAL="$TOOLS/uv/uv"

if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
else
    if [ ! -x "$UV_LOCAL" ]; then
        mkdir -p "$TOOLS/uv"
        case "$(uname -s)-$(uname -m)" in
            Linux-x86_64) TRIPLE="x86_64-unknown-linux-gnu" ;;
            Linux-aarch64|Linux-arm64) TRIPLE="aarch64-unknown-linux-gnu" ;;
            Darwin-x86_64) TRIPLE="x86_64-apple-darwin" ;;
            Darwin-arm64) TRIPLE="aarch64-apple-darwin" ;;
            *)
                echo "Plataforma no soportada: $(uname -s)-$(uname -m)" >&2
                echo "Instalá uv manualmente: https://docs.astral.sh/uv/" >&2
                exit 1
                ;;
        esac
        URL="https://github.com/astral-sh/uv/releases/latest/download/uv-${TRIPLE}.tar.gz"
        echo "Descargando uv para ${TRIPLE}..."
        curl -fsSL "$URL" | tar -xz -C "$TOOLS/uv" --strip-components=1
    fi
    UV_BIN="$UV_LOCAL"
fi

export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
export UV_CACHE_DIR="$TOOLS/uv-cache"
export UV_PYTHON_PREFERENCE="only-managed"
mkdir -p "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR"

echo "Instalando Python 3.12 administrado por uv..."
"$UV_BIN" python install 3.12

if [ ! -x "$SKILL_DIR/.venv/bin/python" ]; then
    echo "Creando entorno virtual..."
    "$UV_BIN" venv --python 3.12 "$SKILL_DIR/.venv"
fi

echo "Instalando dependencias (puede tardar varios minutos)..."
"$UV_BIN" pip install --python "$SKILL_DIR/.venv/bin/python" -r "$SKILL_DIR/requirements.txt"

# These packages pin old Pillow/NumPy versions in their metadata, but their
# code works with the modern stack. Install them without letting pip downgrade.
"$UV_BIN" pip install --python "$SKILL_DIR/.venv/bin/python" --no-deps \
    "rapidocr_onnxruntime==1.4.4" \
    "simple-lama-inpainting==0.1.2"

echo ""
echo "Entorno listo."
echo "Python: $SKILL_DIR/.venv/bin/python"
echo "Modelos: $SKILL_DIR/models"

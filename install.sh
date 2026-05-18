#!/bin/bash
# Multiclaw installer for Ubuntu Linux 22.04+
set -e

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_TARGET="/usr/local/bin/multiclaw"
MULTICLAW_DIR="$HOME/.multiclaw"

echo ""
echo "  Installing Multiclaw..."
echo "  Source : $INSTALL_DIR"
echo "  Config : $MULTICLAW_DIR"
echo ""

# Python check
if ! command -v python3 &>/dev/null; then
    echo "  ERROR: python3 not found. Install: sudo apt install python3"
    exit 1
fi

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python : $PYTHON_VER"

# Install deps
echo "  Installing Python dependencies..."
pip3 install --break-system-packages --quiet -r "$INSTALL_DIR/requirements.txt" 2>/dev/null \
  || pip3 install --quiet -r "$INSTALL_DIR/requirements.txt"

# Create ~/.multiclaw structure
mkdir -p "$MULTICLAW_DIR/agents"
if [ ! -f "$MULTICLAW_DIR/agents.list" ]; then
    echo '{"agents": []}' > "$MULTICLAW_DIR/agents.list"
fi
if [ ! -f "$MULTICLAW_DIR/multiclaw.json" ]; then
    cat > "$MULTICLAW_DIR/multiclaw.json" <<'JSON'
{
  "version": "0.1.0",
  "gateway": {"mode": "local"},
  "tools": {"web": {"search": {"enabled": false}}}
}
JSON
fi

# Create global skills dir
mkdir -p "$MULTICLAW_DIR/skills"

# Create executable wrapper
cat > "$INSTALL_DIR/multiclaw" <<WRAPPER
#!/bin/bash
exec python3 "$INSTALL_DIR/multiclaw.py" "\$@"
WRAPPER
chmod +x "$INSTALL_DIR/multiclaw"

# Symlink to /usr/local/bin
if [ -w "$(dirname $BIN_TARGET)" ]; then
    ln -sf "$INSTALL_DIR/multiclaw" "$BIN_TARGET"
    echo "  ✓ Symlink: $BIN_TARGET"
else
    echo "  ! No write access to /usr/local/bin — run as root or:"
    echo "    sudo ln -sf $INSTALL_DIR/multiclaw $BIN_TARGET"
fi

echo ""
echo "  ✓ Multiclaw installed!"
echo ""
echo "  Usage:"
echo "    multiclaw configure   — управление ботами"
echo "    multiclaw status      — статус всех ботов"
echo "    multiclaw start <bot> — запустить бота"
echo "    multiclaw stop <bot>  — остановить бота"
echo ""

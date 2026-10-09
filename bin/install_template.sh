#!/usr/bin/env bash
# Install the Feishu bridge template into a runtime directory.
# Usage:
#   install_template.sh [target_dir] [--install-deps]
# Default target: ~/workspace/feishu-bridge
# Safe defaults: never creates credentials.json or config.json; existing
# runtime files are preserved because the template ships only *.example.
set -euo pipefail

TARGET="${1:-$HOME/workspace/feishu-bridge}"
if [[ "${1:-}" != "" && "${1:-}" != --install-deps ]]; then
  shift
else
  TARGET="$HOME/workspace/feishu-bridge"
fi
INSTALL_DEPS=0
for arg in "$@"; do
  if [[ "$arg" == "--install-deps" ]]; then INSTALL_DEPS=1; fi
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="$SKILL_DIR/assets/template"

if [[ ! -d "$SRC" ]]; then
  echo "template not found: $SRC" >&2
  exit 2
fi

mkdir -p "$TARGET"
cp -a "$SRC"/. "$TARGET"/
mkdir -p "$TARGET/bridge" "$TARGET/hooks" "$TARGET/ops" \
  "$TARGET/queue/inbox" "$TARGET/queue/outbox" "$TARGET/queue/processed" \
  "$TARGET/state" "$TARGET/logs" "$TARGET/test_files"

if [[ ! -f "$TARGET/config.json" && -f "$TARGET/config.example.json" ]]; then
  cp "$TARGET/config.example.json" "$TARGET/config.json"
  echo "created config.json from example (fill non-secret fields)"
fi

chmod +x "$TARGET/bridge/start_bridge.sh" "$TARGET/hooks/"*.sh 2>/dev/null || true
python3 -m py_compile "$TARGET/bridge/"*.py

if [[ "$INSTALL_DEPS" == "1" ]]; then
  python3 -m venv "$TARGET/.venv"
  "$TARGET/.venv/bin/python" -m pip install -q -r "$TARGET/requirements.txt"
  "$TARGET/.venv/bin/python" -c "import lark_oapi; print('lark_oapi ok')"
else
  echo "deps not installed; create .venv and install requirements.txt before starting"
fi

echo "template installed at $TARGET"
echo "next: choose credential mode, fill config.json, then follow CHECKLIST.md"

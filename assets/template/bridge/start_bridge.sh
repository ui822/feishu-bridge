#!/usr/bin/env bash
# Idempotent Feishu bridge starter.
# - If a supervisor is already running, do nothing.
# - Otherwise launch it detached, reading credentials from the local
#   owner-only credentials file (the supervisor never logs secrets).
# Used by humans, the health hook, and post-reboot recovery alike.
set -u
BASE="$HOME/workspace/feishu-bridge"
LOG="$BASE/logs/supervisor_stdout.log"
mkdir -p "$BASE/logs" "$BASE/state" "$BASE/queue/inbox" "$BASE/queue/outbox" "$BASE/queue/processed"

if pgrep -f "bridge/supervisor.py" >/dev/null 2>&1; then
  echo "already_running"
  exit 0
fi

if [ ! -f "$BASE/credentials.json" ]; then
  echo "missing_credentials_file"
  exit 2
fi

nohup setsid "$BASE/.venv/bin/python" -u "$BASE/bridge/supervisor.py" \
  --credentials-file "$BASE/credentials.json" >>"$LOG" 2>&1 &
echo "started pid=$!"

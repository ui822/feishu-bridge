#!/usr/bin/env bash
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

INBOX="$HOME/workspace/feishu-bridge/queue/inbox"

# Poll the local Feishu bridge inbox only. No secrets, no network calls.
if [[ ! -d "$INBOX" ]]; then
  log "inbox_missing" "{\"path\":\"queue/inbox\"}"
  silent "feishu inbox directory not present yet" "{}"
  exit 0
fi

# Collect inbox json files (oldest first). Avoid state writes: worker
# removes each inbox file after processing, so file presence is the
# detection signal and a failed worker run will be retried next poll.
shopt -s nullglob
files=("$INBOX"/*.json)
if [[ ${#files[@]} -eq 0 ]]; then
  silent "no new feishu messages" "{}"
  exit 0
fi

first="${files[0]}"
count="${#files[@]}"
first_name="$(basename "$first")"

# Build a small JSON payload without embedding message bodies; the worker
# reads the inbox files itself.
payload="$(python3 - "$first_name" "$count" <<'PY'
import json, sys
print(json.dumps({"first_file": sys.argv[1], "count": int(sys.argv[2])}, ensure_ascii=False))
PY
)"

log "inbox_detected" "$payload"
wake "new Feishu message(s) waiting in bridge inbox" "$payload"

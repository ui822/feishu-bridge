#!/usr/bin/env bash
set -u
source "$HATCH_HOOK_RUNTIME"
# Feishu bridge health detector: silent while the daemon heartbeat is
# fresh. Wakes Muse when the heartbeat has been stale for >120s,
# at most once per 30 minutes (hook state), so post-reboot recovery
# can relaunch the bridge from its local credentials file.
HB="$HOME/workspace/feishu-bridge/state/heartbeat.json"
NOW="$(date +%s)"
AGE=999999
if [[ -f "$HB" ]]; then
  TS="$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("ts",0)))' "$HB" 2>/dev/null || echo 0)"
  if [[ "$TS" =~ ^[0-9]+$ ]] && [[ "$TS" -gt 0 ]]; then
    AGE=$((NOW - TS))
  fi
fi

if [[ "$AGE" -le 120 ]]; then
  state="$(hook_state_set '{"alerted_at":0}')" || true
  silent "feishu bridge heartbeat fresh" "{\"age_seconds\":$AGE}"
fi

STATE="$(hook_state_get)"
LAST="$(printf '%s' "$STATE" | python3 -c 'import json,sys
try:
  print(int(json.load(sys.stdin).get("alerted_at",0)))
except Exception:
  print(0)' 2>/dev/null || echo 0)"
if [[ "$LAST" =~ ^[0-9]+$ ]] && [[ "$LAST" -gt 0 ]] && [[ $((NOW - LAST)) -lt 1800 ]]; then
  silent "feishu bridge down but already alerted recently" "{\"age_seconds\":$AGE}"
fi

hook_state_set "{\"alerted_at\":$NOW}" || true
log "feishu_bridge_down" "{\"age_seconds\":$AGE}"
wake "feishu bridge heartbeat stale" "{\"age_seconds\":$AGE}"

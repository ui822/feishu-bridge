#!/usr/bin/env python3
"""Backfill one already-delivered Feishu message into the local bridge.

Use this only for a message the bot can legitimately access. The script
reads the local credentials file itself, never prints credential values,
and by default only downloads incoming resources for inspection. With
--enqueue it also claims the message_id and writes the same inbox JSON
shape produced by bridge_daemon.py.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "bridge"))

import bridge_daemon as bd  # noqa: E402
from file_queue import FileQueue  # noqa: E402


def _get_message(token_cache: bd.TokenCache, message_id: str) -> dict:
    url = f"{bd.BASE}/open-apis/im/v1/messages/{message_id}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token_cache.get()}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != 0:
        raise RuntimeError(f"get message failed code={data.get('code')} msg={data.get('msg')}")
    items = ((data.get("data") or {}).get("items") or [])
    if not items:
        raise RuntimeError("get message returned no items")
    for item in items:
        if item.get("message_id") == message_id:
            return item
    return items[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials-file", default=str(BASE_DIR / "credentials.json"))
    parser.add_argument("--message-id", required=True)
    parser.add_argument("--enqueue", action="store_true", help="claim and write queue/inbox JSON")
    args = parser.parse_args()

    creds = json.loads(Path(args.credentials_file).read_text(encoding="utf-8"))
    config = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
    token_cache = bd.TokenCache(str(creds["app_id"]), str(creds["app_secret"]))
    creds = {}

    item = _get_message(token_cache, args.message_id)
    body = item.get("body") or {}
    content_raw = body.get("content") or "{}"
    try:
        content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
    except Exception:
        content = {}
    message_type = str(item.get("msg_type") or "")
    text, resources, unsupported_reason = bd.parse_incoming_content(message_type, content)

    sender = item.get("sender") or {}
    sender_open_id = str(sender.get("id") or "")
    chat_id = str(item.get("chat_id") or "")
    allowed_open_ids = set(config.get("allowed_open_ids") or [])
    allowed_chat_ids = set(config.get("allowed_chat_ids") or [])
    if allowed_open_ids and sender_open_id not in allowed_open_ids:
        raise RuntimeError("backfill refused: sender is not whitelisted")
    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        raise RuntimeError("backfill refused: chat is not whitelisted")

    attachments, attachment_errors = bd.download_incoming_attachments(
        token_cache, args.message_id, resources
    )

    payload = {
        "message_id": args.message_id,
        "chat_id": chat_id,
        "chat_type": "",
        "sender_open_id": sender_open_id,
        "message_type": message_type,
        "text": text,
        "attachments": attachments,
        "attachment_errors": attachment_errors,
        "unsupported_reason": unsupported_reason,
        "create_time": str(item.get("create_time") or ""),
        "backfilled": True,
    }

    enqueued = False
    if args.enqueue:
        queue = FileQueue(BASE_DIR / (config.get("queue_dir") or "queue"))
        if not queue.claim(args.message_id):
            raise RuntimeError("backfill refused: message_id already claimed")
        queue.enqueue_inbox(payload)
        enqueued = True

    print(
        json.dumps(
            {
                "ok": True,
                "message_type": message_type,
                "text_present": bool(text),
                "attachments": attachments,
                "attachment_errors": attachment_errors,
                "unsupported_reason": unsupported_reason,
                "enqueued": enqueued,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not attachment_errors else 3


if __name__ == "__main__":
    raise SystemExit(main())

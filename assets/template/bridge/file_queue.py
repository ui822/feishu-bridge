"""Local file queue for the Feishu <-> agent bridge.

Messages are stored as one JSON file per message id, with persistent
message_id de-duplication. No credentials are read or stored here.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class FileQueue:
    def __init__(self, queue_dir: Path) -> None:
        self.inbox = queue_dir / "inbox"
        self.outbox = queue_dir / "outbox"
        self.processed = queue_dir / "processed"
        self.state_file = queue_dir.parent / "state" / "seen_message_ids.json"
        for d in (self.inbox, self.outbox, self.processed, self.state_file.parent):
            d.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        if self.state_file.exists():
            try:
                self._seen = set(json.loads(self.state_file.read_text(encoding="utf-8")))
            except Exception:
                self._seen = set()

    def claim(self, message_id: str) -> bool:
        """Return True only the first time a message_id is claimed."""
        if not message_id or message_id in self._seen:
            return False
        self._seen.add(message_id)
        _atomic_write_json(self.state_file, sorted(self._seen))
        return True

    def enqueue_inbox(self, payload: dict) -> Path:
        message_id = payload.get("message_id") or "unknown"
        path = self.inbox / f"{message_id}.json"
        _atomic_write_json(path, payload)
        return path

    def enqueue_outbox(self, payload: dict) -> Path:
        reply_to = payload.get("reply_to_message_id") or payload.get("chat_id") or "reply"
        path = self.outbox / f"{reply_to}.json"
        _atomic_write_json(path, payload)
        return path

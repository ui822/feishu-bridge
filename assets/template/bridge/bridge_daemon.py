"""Feishu <-> agent bridge daemon (long-connection mode).

Startup contract:
- Reads config.json (non-secret fields only: app_id, queue_dir, whitelist).
- Reads ONE JSON line from stdin at startup: {"app_id": ..., "app_secret": ...}
  The supervisor supplies that line over a pipe. This daemon never reads
  a credential file itself and never writes the secret to disk or logs.
  Whether the supervisor sources credentials from stdin or from a local
  owner-only credentials file is the operator's explicit deployment
  choice; see the skill's credential gate.
- Starts a Feishu WS long-connection for im.message.receive_v1, enqueues
  normalized messages to queue/inbox after message_id de-duplication.
- Watches queue/outbox for Muse replies and sends them back to the
  originating chat_id, moving each file to queue/processed when done.

Logging is sanitized: no secrets, no full tokens.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "logs" / "daemon.log"

BASE = "https://open.feishu.cn"
TOKEN_URL = f"{BASE}/open-apis/auth/v3/tenant_access_token/internal"
SEND_URL = f"{BASE}/open-apis/im/v1/messages?receive_id_type=chat_id"
INCOMING_ATTACHMENTS_DIR = BASE_DIR / "queue" / "incoming_attachments"
MAX_INCOMING_ATTACHMENT_BYTES = 100 * 1024 * 1024


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def get_attr(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class TokenCache:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            now = time.time()
            if self._token and now < self._expires_at - 300:
                return self._token
            body = json.dumps(
                {"app_id": self._app_id, "app_secret": self._app_secret}
            ).encode("utf-8")
            req = urllib.request.Request(
                TOKEN_URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") != 0:
                raise RuntimeError(f"token exchange failed code={data.get('code')} msg={data.get('msg')}")
            self._token = data["tenant_access_token"]
            self._expires_at = now + int(data.get("expire") or 7200)
            return self._token


def _safe_filename(name: object, fallback: str) -> str:
    raw = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    raw = re.sub(r"[^\w.-]+", "_", raw, flags=re.UNICODE).strip("._")
    return raw[:120] or fallback


def _extension_for(filename: object, content_type: str, kind: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix and len(suffix) <= 10:
        return suffix
    guessed = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip())
    if guessed:
        return ".jpg" if guessed == ".jpe" else guessed
    return {"video": ".mp4", "image": ".png", "audio": ".mp3"}.get(kind, ".bin")


def download_message_resource(
    token_cache: TokenCache, message_id: str, resource: dict, index: int = 1
) -> dict:
    """Download one user-sent Feishu message resource to local storage.

    The returned dict deliberately contains the local path and display
    metadata only; the Feishu resource key stays inside this process.
    """
    kind = str(resource.get("kind") or "file")
    resource_key = str(resource.get("resource_key") or "")
    resource_type = str(resource.get("resource_type") or "file")
    original_name = str(resource.get("file_name") or "")
    if not message_id or not resource_key:
        raise RuntimeError("missing message resource key")

    url = (
        f"{BASE}/open-apis/im/v1/messages/"
        f"{urllib.parse.quote(message_id, safe='')}/resources/"
        f"{urllib.parse.quote(resource_key, safe='')}?type="
        f"{urllib.parse.quote(resource_type, safe='')}"
    )
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token_cache.get()}"},
        method="GET",
    )
    dest_dir = INCOMING_ATTACHMENTS_DIR / _safe_filename(message_id, "message")
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        response_ctx = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        code = None
        msg = f"http {e.code}"
        try:
            data = json.loads(e.read().decode("utf-8"))
            code = data.get("code")
            msg = str(data.get("msg") or msg)
        except Exception:
            pass
        raise RuntimeError(f"download failed code={code} msg={msg}") from None

    with response_ctx as resp:
        content_type = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip()
        if content_type == "application/json":
            try:
                data = json.loads(resp.read().decode("utf-8"))
                raise RuntimeError(
                    f"download failed code={data.get('code')} msg={data.get('msg')}"
                )
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError("download failed: unexpected JSON response") from None
        content_length = resp.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_INCOMING_ATTACHMENT_BYTES:
                    raise RuntimeError("download failed: resource exceeds 100 MB limit")
            except ValueError:
                pass

        safe_name = _safe_filename(original_name, kind)
        if not Path(safe_name).suffix:
            safe_name += _extension_for(original_name, content_type, kind)
        dest = dest_dir / f"{index:02d}-{safe_name}"
        tmp = dest.with_name(dest.name + ".part")
        size = 0
        try:
            with tmp.open("wb") as f:
                while True:
                    chunk = resp.read(1024 * 64)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_INCOMING_ATTACHMENT_BYTES:
                        raise RuntimeError("download failed: resource exceeds 100 MB limit")
                    f.write(chunk)
            if size == 0:
                raise RuntimeError("download failed: empty resource")
            os.chmod(tmp, 0o600)
            tmp.replace(dest)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    attachment = {
        "type": kind,
        "path": str(dest.relative_to(BASE_DIR)),
        "file_name": original_name or dest.name,
        "mime_type": content_type or mimetypes.guess_type(dest.name)[0] or "application/octet-stream",
        "size_bytes": size,
        "source": "feishu",
    }
    if resource.get("duration") is not None:
        attachment["duration"] = resource.get("duration")
    if resource.get("cover_image_key"):
        attachment["has_cover_image"] = True
    return attachment


def _walk_post_content(node: object, texts: list[str], image_keys: list[str]) -> None:
    if isinstance(node, dict):
        tag = str(node.get("tag") or "")
        if tag == "text" and isinstance(node.get("text"), str):
            texts.append(node["text"])
        if tag in {"img", "image"} and node.get("image_key"):
            image_keys.append(str(node["image_key"]))
        if isinstance(node.get("title"), str) and node["title"]:
            texts.append(node["title"])
        for value in node.values():
            _walk_post_content(value, texts, image_keys)
    elif isinstance(node, list):
        for item in node:
            _walk_post_content(item, texts, image_keys)


def parse_incoming_content(message_type: str, content: dict) -> tuple[str, list[dict], str]:
    """Normalize a Feishu message body into text plus downloadable resources."""
    msg_type = (message_type or "").strip().lower()
    if msg_type == "text":
        return str(content.get("text") or "").strip(), [], ""

    if msg_type == "post":
        texts: list[str] = []
        image_keys: list[str] = []
        _walk_post_content(content, texts, image_keys)
        resources = [
            {"kind": "image", "resource_key": key, "resource_type": "image"}
            for key in dict.fromkeys(image_keys)
        ]
        return "\n".join(t.strip() for t in texts if t.strip()), resources, ""

    if msg_type == "image":
        key = str(content.get("image_key") or "")
        if key:
            return "", [{"kind": "image", "resource_key": key, "resource_type": "image"}], ""
        return "", [], "image message without image_key"

    if msg_type in {"file", "audio"}:
        key = str(content.get("file_key") or "")
        if key:
            resource = {
                "kind": msg_type,
                "resource_key": key,
                "resource_type": "file",
                "file_name": str(content.get("file_name") or ""),
            }
            if content.get("duration") is not None:
                resource["duration"] = content.get("duration")
            return "", [resource], ""
        return "", [], f"{msg_type} message without file_key"

    if msg_type == "media":
        key = str(content.get("file_key") or "")
        if key:
            resource = {
                "kind": "video",
                "resource_key": key,
                "resource_type": "file",
                "file_name": str(content.get("file_name") or ""),
            }
            if content.get("duration") is not None:
                resource["duration"] = content.get("duration")
            if content.get("image_key"):
                resource["cover_image_key"] = str(content["image_key"])
            return "", [resource], ""
        return "", [], "media message without file_key"

    text = str(content.get("text") or "").strip()
    if text:
        return text, [], ""
    if msg_type:
        return "", [], f"unsupported message_type={msg_type}"
    return "", [], ""


def download_incoming_attachments(
    token_cache: TokenCache, message_id: str, resources: list[dict]
) -> tuple[list[dict], list[dict]]:
    attachments: list[dict] = []
    errors: list[dict] = []
    for index, resource in enumerate(resources, start=1):
        try:
            attachments.append(download_message_resource(token_cache, message_id, resource, index))
        except Exception as exc:
            errors.append(
                {
                    "type": str(resource.get("kind") or "file"),
                    "file_name": str(resource.get("file_name") or ""),
                    "stage": "download",
                    "msg": str(exc)[:240],
                }
            )
    return attachments, errors


def _multipart(fields: dict, file_field: str, filename: str, data: bytes, content_type: str) -> tuple[bytes, str]:
    import uuid

    boundary = "----feishu-bridge-" + uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
    parts.append(data)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def _post_multipart(url: str, token: str, fields: dict, file_field: str, path: Path, content_type: str) -> dict:
    data = path.read_bytes()
    body, boundary = _multipart(fields, file_field, path.name, data, content_type)
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"code": -1, "msg": f"http {e.code}"}


def upload_image(token_cache: TokenCache, path: Path) -> dict:
    token = token_cache.get()
    return _post_multipart(
        f"{BASE}/open-apis/im/v1/images",
        token,
        {"image_type": "message"},
        "image",
        path,
        "image/png" if path.suffix.lower() == ".png" else "image/jpeg",
    )


def upload_file(token_cache: TokenCache, path: Path) -> dict:
    token = token_cache.get()
    suffix = path.suffix.lower().lstrip(".")
    file_type_map = {
        "mp4": "mp4",
        "pdf": "pdf",
        "xls": "xls",
        "xlsx": "xls",
        "doc": "doc",
        "docx": "doc",
        "opus": "opus",
    }
    file_type = file_type_map.get(suffix, "stream")
    return _post_multipart(
        f"{BASE}/open-apis/im/v1/files",
        token,
        {"file_type": file_type, "file_name": path.name},
        "file",
        path,
        "application/octet-stream",
    )


def send_message(token_cache: TokenCache, chat_id: str, msg_type: str, content_obj: dict) -> dict:
    token = token_cache.get()
    body = json.dumps(
        {"receive_id": chat_id, "msg_type": msg_type, "content": json.dumps(content_obj, ensure_ascii=False)},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        SEND_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"code": -1, "msg": f"http {e.code}"}


def send_attachment(token_cache: TokenCache, chat_id: str, kind: str, file_path: str) -> dict:
    """Upload one local file and send it. kind: 'file' | 'image'."""
    path = Path(file_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.is_file():
        return {"code": -2, "msg": "attachment file not found", "stage": "local"}
    try:
        if kind == "image":
            up = upload_image(token_cache, path)
            key = ((up.get("data") or {}).get("image_key")) if isinstance(up, dict) else None
            if up.get("code") != 0 or not key:
                return {"code": up.get("code"), "msg": up.get("msg"), "stage": "upload"}
            return {**send_message(token_cache, chat_id, "image", {"image_key": key}), "stage": "send"}
        up = upload_file(token_cache, path)
        key = ((up.get("data") or {}).get("file_key")) if isinstance(up, dict) else None
        if up.get("code") != 0 or not key:
            return {"code": up.get("code"), "msg": up.get("msg"), "stage": "upload"}
        # Videos uploaded with file_type=mp4 must go out as a "media"
        # message, not a "file" message (Feishu error 230055 otherwise).
        if kind in ("video", "media") or path.suffix.lower() == ".mp4":
            return {**send_message(token_cache, chat_id, "media", {"file_key": key}), "stage": "send"}
        return {**send_message(token_cache, chat_id, "file", {"file_key": key}), "stage": "send"}
    except Exception as exc:
        return {"code": -3, "msg": type(exc).__name__, "stage": "exception"}


def send_text(token_cache: TokenCache, chat_id: str, text: str) -> dict:
    token = token_cache.get()
    body = json.dumps(
        {"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        SEND_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"code": -1, "msg": f"http {e.code}"}


HEARTBEAT_PATH = BASE_DIR / "state" / "heartbeat.json"
_started_at = time.time()


def heartbeat_loop(stop: threading.Event) -> None:
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    while not stop.is_set():
        try:
            payload = {
                "ts": int(time.time()),
                "pid": __import__("os").getpid(),
                "started_at": int(_started_at),
                "uptime_seconds": int(time.time() - _started_at),
            }
            tmp = HEARTBEAT_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(HEARTBEAT_PATH)
        except Exception:
            pass
        stop.wait(15.0)


def outbox_loop(config: dict, token_cache: TokenCache, stop: threading.Event) -> None:
    queue_dir = BASE_DIR / (config.get("queue_dir") or "queue")
    outbox = queue_dir / "outbox"
    processed = queue_dir / "processed"
    outbox.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)
    log("outbox watcher started")
    while not stop.is_set():
        try:
            for path in sorted(outbox.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    chat_id = payload.get("chat_id") or ""
                    text = payload.get("text") or ""
                    attachments = payload.get("attachments") or []
                    # Single-file conveniences used by simple producers.
                    if payload.get("image_path"):
                        attachments.append({"type": "image", "path": payload["image_path"]})
                    if payload.get("file_path"):
                        attachments.append({"type": "file", "path": payload["file_path"]})
                    if not chat_id or (not text and not attachments):
                        log(f"outbox skip incomplete file={path.name}")
                    else:
                        failures: list[dict] = []
                        if text:
                            resp = send_text(token_cache, chat_id, text)
                            if resp.get("code") == 0:
                                log(f"outbox sent text file={path.name} chat_id_present=True")
                            else:
                                failures.append({"code": resp.get("code"), "msg": resp.get("msg"), "stage": "text"})
                                log(f"outbox text failed file={path.name} code={resp.get('code')} msg={resp.get('msg')}")
                        for att in attachments:
                            if not isinstance(att, dict):
                                continue
                            kind = att.get("type") or "file"
                            fpath = att.get("path") or ""
                            resp = send_attachment(token_cache, chat_id, kind, fpath)
                            if resp.get("code") == 0:
                                log(f"outbox sent attachment kind={kind} file={path.name}")
                            else:
                                failures.append(
                                    {"code": resp.get("code"), "msg": resp.get("msg"), "stage": resp.get("stage"), "kind": kind}
                                )
                                log(
                                    f"outbox attachment failed kind={kind} stage={resp.get('stage')} "
                                    f"code={resp.get('code')} msg={resp.get('msg')} file={path.name}"
                                )
                        if failures:
                            (BASE_DIR / "state" / "last_send_error.json").write_text(
                                json.dumps(failures, ensure_ascii=False),
                                encoding="utf-8",
                            )
                        else:
                            log(f"outbox sent file={path.name} chat_id_present=True")
                    path.replace(processed / path.name)
                except Exception as exc:  # keep loop alive; sanitized message only
                    log(f"outbox error file={path.name} error={type(exc).__name__}")
        except Exception as exc:
            log(f"outbox loop error={type(exc).__name__}")
        stop.wait(2.0)


def main() -> int:
    config = load_config()
    line = sys.stdin.readline()
    if not line.strip():
        log("startup failed: no credentials on stdin")
        return 2
    try:
        creds = json.loads(line)
        app_id = str(creds["app_id"])
        app_secret = str(creds["app_secret"])
    except Exception:
        log("startup failed: invalid credential JSON on stdin")
        return 2
    # stdin is no longer needed; secret lives only in these variables / TokenCache.
    if app_id != (config.get("app_id") or ""):
        log("startup note: stdin app_id differs from config app_id; using stdin app_id")

    sys.path.insert(0, str(BASE_DIR / "bridge"))
    from file_queue import FileQueue  # local bridge/file_queue.py (not stdlib queue)

    queue = FileQueue(BASE_DIR / (config.get("queue_dir") or "queue"))
    token_cache = TokenCache(app_id, app_secret)

    stop = threading.Event()
    t = threading.Thread(target=outbox_loop, args=(config, token_cache, stop), daemon=True)
    t.start()
    hb = threading.Thread(target=heartbeat_loop, args=(stop,), daemon=True)
    hb.start()
    log("heartbeat started")

    # Import SDK here so --help / py_compile does not require it.
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

    def on_message(data: P2ImMessageReceiveV1) -> None:
        try:
            event = get_attr(data, "event")
            message = get_attr(event, "message")
            sender = get_attr(event, "sender")
            sender_id = get_attr(sender, "sender_id")
            open_id = get_attr(sender_id, "open_id") or ""
            chat_id = get_attr(message, "chat_id") or ""
            chat_type = get_attr(message, "chat_type") or ""
            message_id = get_attr(message, "message_id") or ""
            message_type = str(get_attr(message, "message_type") or "")
            content_raw = get_attr(message, "content") or "{}"
            try:
                if isinstance(content_raw, str):
                    content = json.loads(content_raw)
                elif isinstance(content_raw, dict):
                    content = content_raw
                else:
                    content = {}
            except Exception:
                content = {}
            text, resources, unsupported_reason = parse_incoming_content(message_type, content)

            # Reload whitelist from disk per message so backfilling
            # allowed_open_ids takes effect on the next process start
            # without code changes, and stays current in config.json.
            try:
                fresh = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                allowed = set(fresh.get("allowed_open_ids") or [])
            except Exception:
                allowed = set(config.get("allowed_open_ids") or [])
            if allowed and open_id not in allowed:
                log(f"inbox ignored non-whitelisted sender message_id={message_id}")
                return
            if not text and not resources and not unsupported_reason:
                log(f"inbox ignored empty message_id={message_id} type={message_type}")
                return
            if not queue.claim(message_id):
                log(f"inbox duplicate message_id={message_id}")
                return
            attachments, attachment_errors = download_incoming_attachments(
                token_cache, message_id, resources
            )
            payload = {
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": chat_type,
                "sender_open_id": open_id,
                "message_type": message_type,
                "text": text,
                "attachments": attachments,
                "attachment_errors": attachment_errors,
                "unsupported_reason": unsupported_reason,
                "create_time": str(get_attr(message, "create_time") or ""),
            }
            queue.enqueue_inbox(payload)
            # Record first sender for whitelist backfill (open_id is not a secret).
            try:
                (BASE_DIR / "state" / "last_sender.json").write_text(
                    json.dumps({"sender_open_id": open_id, "chat_id": chat_id}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
            log(
                f"inbox enqueued message_id={message_id} type={message_type} "
                f"attachments={len(attachments)} attachment_errors={len(attachment_errors)} "
                f"chat_id_present={bool(chat_id)}"
            )
        except Exception as exc:
            log(f"on_message error={type(exc).__name__}")

    # Environment note: this runtime reaches the internet via an HTTP
    # proxy from the environment. The SDK forces websockets proxy=None
    # (direct), which fails here with SSL WRONG_VERSION_NUMBER. Restore
    # websockets' default proxy discovery (proxy=True reads env) without
    # logging any proxy URL or credentials.
    try:
        import lark_oapi.ws.client as _ws_mod

        _ws_mod._ws_connect_kwargs = lambda: {"proxy": True}  # type: ignore[attr-defined]
        log("ws proxy discovery enabled from environment")
    except Exception as exc:
        log(f"ws proxy patch failed error={type(exc).__name__}")

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )

    # WS reconnect loop: the SDK already reconnects after a successful
    # first connection, but if start() ever returns or raises, recreate
    # the client and retry with capped backoff. The secret stays only in
    # this process's memory across every attempt.
    attempt = 0
    try:
        while not stop.is_set():
            attempt += 1
            run_started = time.time()
            log(f"starting Feishu WS long connection attempt={attempt}")
            ws_client = lark.ws.Client(
                app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.INFO
            )
            try:
                ws_client.start()
                log("Feishu WS start returned unexpectedly; will reconnect")
            except Exception as exc:
                log(f"Feishu WS failed error={type(exc).__name__}; will reconnect")
            if stop.is_set():
                break
            # A long-lived run resets backoff; short failures back off.
            if time.time() - run_started > 300:
                attempt = 0
            delay = min(60, 5 * max(1, attempt))
            log(f"ws reconnect in {delay}s")
            stop.wait(delay)
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

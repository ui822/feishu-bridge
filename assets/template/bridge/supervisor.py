"""Feishu bridge supervisor (keepalive).

Credential source is either one JSON line on stdin, or
`--credentials-file <path>` reading a local JSON file with owner-only
permissions when the operator explicitly chooses full auto-recovery.
Credentials are held in this process's memory, fed to each daemon child
over a pipe, and never logged. Supervisor starts bridge_daemon.py as a
child; if the child exits it is restarted with capped exponential
backoff. If the supervisor itself dies, a health hook can relaunch it
from the credentials file via bridge/start_bridge.sh.

Logs are sanitized: only pids, exit codes, durations, restart counts.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DAEMON = BASE_DIR / "bridge" / "bridge_daemon.py"
PYTHON = BASE_DIR / ".venv" / "bin" / "python"
LOG_PATH = BASE_DIR / "logs" / "supervisor.log"
HEARTBEAT = BASE_DIR / "state" / "supervisor_heartbeat.json"
CHILD_STATE = BASE_DIR / "state" / "daemon_child.json"

_stop = False


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def write_json(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _handle_signal(signum, frame):  # noqa: ANN001
    global _stop
    _stop = True


def main() -> int:
    # Credential source (operator's explicit deployment choice):
    #   supervisor.py --credentials-file <path>   read JSON file (0600)
    #   supervisor.py                             read one JSON line from stdin
    # Either way the secret is then held only in this process's memory
    # and fed to each child over a pipe. It is never logged.
    creds: dict = {}
    if len(sys.argv) >= 3 and sys.argv[1] == "--credentials-file":
        try:
            creds = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        except Exception:
            log("supervisor startup failed: cannot read credentials file")
            return 2
    else:
        line = sys.stdin.readline()
        if not line.strip():
            log("supervisor startup failed: no credentials on stdin")
            return 2
        try:
            creds = json.loads(line)
        except Exception:
            log("supervisor startup failed: invalid credential JSON")
            return 2
        del line
    try:
        app_id = str(creds["app_id"])
        app_secret = str(creds["app_secret"])
    except Exception:
        log("supervisor startup failed: credentials missing fields")
        return 2
    cred_line = json.dumps({"app_id": app_id, "app_secret": app_secret})
    creds = {}

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    restart_count = 0
    backoff = 3
    log("supervisor started; secret held only in memory")

    while not _stop:
        started = time.time()
        write_json(
            HEARTBEAT,
            {"ts": int(started), "pid": os.getpid(), "restart_count": restart_count, "phase": "starting_child"},
        )
        try:
            child = subprocess.Popen(
                [str(PYTHON), "-u", str(DAEMON)],
                stdin=subprocess.PIPE,
                cwd=str(BASE_DIR),
            )
        except Exception as exc:
            log(f"child spawn failed error={type(exc).__name__}")
            time.sleep(backoff)
            backoff = min(60, backoff * 2)
            continue

        write_json(
            CHILD_STATE,
            {"pid": child.pid, "started_at": int(started), "restart_count": restart_count},
        )
        log(f"daemon child started pid={child.pid} restart_count={restart_count}")
        try:
            assert child.stdin is not None
            child.stdin.write(cred_line.encode("utf-8") + b"\n")
            child.stdin.close()
        except Exception as exc:
            log(f"child credential pipe failed error={type(exc).__name__}")

        # Monitor child; refresh supervisor heartbeat while it lives.
        while not _stop:
            rc = child.poll()
            if rc is not None:
                break
            write_json(
                HEARTBEAT,
                {
                    "ts": int(time.time()),
                    "pid": os.getpid(),
                    "child_pid": child.pid,
                    "restart_count": restart_count,
                    "phase": "child_running",
                    "child_uptime_seconds": int(time.time() - started),
                },
            )
            time.sleep(5)

        if _stop:
            try:
                child.terminate()
                child.wait(timeout=10)
            except Exception:
                try:
                    child.kill()
                except Exception:
                    pass
            log("supervisor stopping; child terminated")
            break

        runtime = time.time() - started
        rc = child.returncode
        restart_count += 1
        log(f"daemon child exited code={rc} runtime_seconds={int(runtime)} restart_count={restart_count}")
        if runtime > 300:
            backoff = 3
        write_json(
            HEARTBEAT,
            {
                "ts": int(time.time()),
                "pid": os.getpid(),
                "restart_count": restart_count,
                "phase": "restarting",
                "backoff_seconds": backoff,
            },
        )
        # Interruptible backoff sleep.
        end = time.time() + backoff
        while not _stop and time.time() < end:
            time.sleep(1)
        backoff = min(60, backoff * 2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

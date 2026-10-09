"""Validate credentials.json by exchanging a tenant token.

Prints only code / expire / sanitized status. Never prints secrets/tokens.
Reads credentials from the local credentials file the bridge itself uses.
"""
import json
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CRED = BASE_DIR / "credentials.json"

creds = json.loads(CRED.read_text(encoding="utf-8"))
body = json.dumps({"app_id": creds["app_id"], "app_secret": creds["app_secret"]}).encode()
req = urllib.request.Request(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=20) as resp:
    data = json.loads(resp.read().decode())
print(json.dumps({"code": data.get("code"), "msg": data.get("msg"), "expire": data.get("expire")}))

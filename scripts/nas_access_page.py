#!/usr/bin/env python3
"""NAS access grant page — sign in once, I get the keys.

Throw this up when the fleet needs access restored (NAS hosts deny SSH by
publickey and the SMB view mis-maps identities). The operator signs in on
http://127.0.0.1:8790/ and the page, in memory only:

  1. installs THIS machine's `~/.ssh/id_ed25519.pub` into the target hosts'
     `authorized_keys` (via one password-authenticated SSH round),
  2. optionally remounts the Electronicdataassets SMB share as that user.

The password is passed to the child processes' stdin and is never written
to disk or logged. Bind is 127.0.0.1 only.

Usage: python3 scripts/nas_access_page.py [--port 8790]
"""
from __future__ import annotations

import argparse
import html
import http.server
import os
import subprocess
import tempfile

HOSTS = {
    "charleybasecamp": ("charleybasecamp.local", "the NAS (charley)"),
    "yosemiteraid5": ("yosemiteraid5.local", "the NVMe raid (yosemite)"),
}
SMB_SHARE = "//charleybasecamp.local/Electronicdataassets"
SMB_MOUNT = "/Users/samkim/Volumes/Electronicdataassets"


def pubkey() -> str:
    for p in (os.path.expanduser("~/.ssh/id_ed25519.pub"),
              os.path.expanduser("~/.ssh/id_rsa.pub")):
        if os.path.exists(p):
            return open(p).read().strip()
    return ""


def expect_run(script: str) -> tuple[bool, str]:
    """Run an expect script; return (ok, combined output)."""
    with tempfile.NamedTemporaryFile("w", suffix=".exp", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run(
            ["expect", path], capture_output=True, text=True, timeout=60
        )
        out = (r.stdout + r.stderr).strip()
        return r.returncode == 0, out[-1200:]
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        os.unlink(path)


def install_key(host: str, user: str, password: str) -> tuple[bool, str]:
    key = pubkey()
    if not key:
        return False, "no ~/.ssh/id_ed25519.pub on this machine"
    remote = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "grep -qF 'nas-access-page' ~/.ssh/authorized_keys 2>/dev/null || "
        f"echo '{key} nas-access-page' >> ~/.ssh/authorized_keys; "
        "chmod 600 ~/.ssh/authorized_keys && echo KEY-INSTALLED"
    )
    script = f"""
set timeout 30
spawn ssh -o StrictHostKeyChecking=accept-new {user}@{host} "{remote}"
expect {{
    "*assword*" {{ send "{password}\\r"; exp_continue }}
    eof
}}
catch wait result
exit [lindex $result 3]
"""
    ok, out = expect_run(script)
    if not ok:
        return False, out
    verify = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
         f"{user}@{host}", "echo KEY-VERIFIED; whoami"],
        capture_output=True, text=True,
    )
    vok = verify.returncode == 0
    return vok, (verify.stdout + verify.stderr).strip() or out


def remount_smb(user: str, password: str) -> tuple[bool, str]:
    subprocess.run(["umount", SMB_MOUNT], capture_output=True)
    os.makedirs(SMB_MOUNT, exist_ok=True)
    script = f"""
set timeout 45
spawn mount_smbfs //{user}@{SMB_SHARE.lstrip("/")} {SMB_MOUNT}
expect {{
    "*assword*" {{ send "{password}\\r"; exp_continue }}
    eof
}}
catch wait result
exit [lindex $result 3]
"""
    ok, out = expect_run(script)
    check = os.path.isdir(os.path.join(SMB_MOUNT, "agent-inbox"))
    return ok and check, out + f"\nmount check agent-inbox: {check}"


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>NAS access</title>
<style>body{{font-family:ui-monospace,monospace;max-width:720px;margin:3rem auto;padding:0 1rem}}
label{{display:block;margin:.6rem 0}}input[type=password],input[type=text]{{width:24rem;padding:.3rem}}
button{{padding:.5rem 1rem;margin-top:1rem}}pre{{background:#111;color:#d8d8d8;padding:1rem;white-space:pre-wrap}}</style>
</head><body>
<h1>NAS access grant</h1>
<p>The fleet needs: an authorized SSH key on the NAS hosts and a credentialed
SMB remount. Sign in below; the password is passed to the child processes
in memory and never written to disk.</p>
<form method="POST" action="/grant">
<label>Username <input type="text" name="user" value="{user}" required></label>
<label>Password <input type="password" name="password" required></label>
<label><input type="checkbox" name="h_charleybasecamp" checked> Install SSH key on charleybasecamp (the NAS)</label>
<label><input type="checkbox" name="h_yosemiteraid5" checked> Install SSH key on yosemiteraid5 (the NVMe raid)</label>
<label><input type="checkbox" name="remount" checked> Remount Electronicdataassets SMB as this user</label>
<button type="submit">Grant access</button>
</form>
<h3>This machine's public key</h3>
<pre>{pub}</pre>
</body></html>"""

RESULT = """<!doctype html><html><head><meta charset="utf-8"><title>NAS access — result</title>
<style>body{{font-family:ui-monospace,monospace;max-width:720px;margin:3rem auto;padding:0 1rem}}
pre{{background:#111;color:#d8d8d8;padding:1rem;white-space:pre-wrap}}</style>
</head><body><h1>Results</h1><pre>{out}</pre><p><a href="/">back</a></p></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: str, status: int = 200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(PAGE.format(user="harness", pub=html.escape(pubkey())))
        else:
            self._send("not found", 404)

    def do_POST(self):
        if self.path != "/grant":
            self._send("not found", 404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        fields = {}
        for pair in self.rfile.read(length).decode().split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                fields[k] = html.unescape(v.replace("+", " "))
        user = fields.get("user", "").strip()
        password = fields.get("password", "")
        if not user or not password:
            self._send("user and password required", 400)
            return
        report = []
        for key, (host, label) in HOSTS.items():
            if fields.get(f"h_{key}") == "on":
                ok, out = install_key(host, user, password)
                report.append(f"== ssh key -> {label} ({host}): {'OK' if ok else 'FAIL'}\n{out}")
        if fields.get("remount") == "on":
            ok, out = remount_smb(user, password)
            report.append(f"== SMB remount {SMB_SHARE}: {'OK' if ok else 'FAIL'}\n{out}")
        self._send(RESULT.format(out=html.escape("\n\n".join(report) or "nothing selected")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    args = ap.parse_args()
    srv = http.server.HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"NAS access page: http://127.0.0.1:{args.port}/  (127.0.0.1 only)")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

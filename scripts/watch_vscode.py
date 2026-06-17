import json
import os
import re
import ssl
import smtplib
import urllib.request
import urllib.error
from pathlib import Path
from email.message import EmailMessage
from datetime import datetime, timezone

STATE = Path(".state/vscode.json")

UPDATE_API = "https://update.code.visualstudio.com/api/releases/stable"
GITHUB_API = "https://api.github.com/repos/microsoft/vscode/releases/latest"
DOWNLOAD_LATEST = "https://update.code.visualstudio.com/latest/win32-x64-user/stable"

VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")
EXE_RE = re.compile(r"VSCodeUserSetup-x64-(\d+\.\d+\.\d+)\.exe", re.I)


def semver(v):
    return tuple(map(int, v.split(".")))


def get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "vscode-release-watch",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_download_redirect():
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(DOWNLOAD_LATEST, method="HEAD", headers={
        "User-Agent": "vscode-release-watch",
    })

    try:
        with opener.open(req, timeout=25) as r:
            text = r.geturl() + "\n" + str(dict(r.headers))
    except urllib.error.HTTPError as e:
        text = (e.headers.get("Location") or "") + "\n" + str(dict(e.headers))

    m = EXE_RE.search(text)
    if m:
        return m.group(1), text

    versions = VERSION_RE.findall(text)
    if versions:
        return sorted(versions, key=semver, reverse=True)[0], text

    return None, text


def get_update_api_latest():
    versions = get_json(UPDATE_API)
    versions = [v for v in versions if VERSION_RE.fullmatch(v)]
    return sorted(versions, key=semver, reverse=True)[0]


def get_github_latest():
    data = get_json(GITHUB_API)
    tag = str(data.get("tag_name", "")).lstrip("v")
    if VERSION_RE.fullmatch(tag):
        return tag, data.get("html_url", "")
    return None, data.get("html_url", "")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def save_state(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def send_mail(subject, body):
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg["Subject"] = subject
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)


def main():
    errors = []

    try:
        update_v = get_update_api_latest()
    except Exception as e:
        update_v = None
        errors.append(f"update_api: {e}")

    try:
        github_v, github_url = get_github_latest()
    except Exception as e:
        github_v, github_url = None, ""
        errors.append(f"github: {e}")

    try:
        download_v, download_probe = probe_download_redirect()
    except Exception as e:
        download_v, download_probe = None, ""
        errors.append(f"download: {e}")

    versions = [v for v in [update_v, github_v, download_v] if v]
    if not versions:
        raise RuntimeError("No version found: " + " | ".join(errors))

    latest = sorted(versions, key=semver, reverse=True)[0]

    if download_v == latest:
        status = "DOWNLOADABLE"
    elif github_v == latest:
        status = "GITHUB_RELEASED"
    else:
        status = "UPDATE_API_CANDIDATE"

    state = load_state()
    last_key = state.get("last_key")
    current_key = f"{latest}:{status}"

    print(json.dumps({
        "latest": latest,
        "status": status,
        "update_api": update_v,
        "github": github_v,
        "download": download_v,
        "errors": errors,
    }, indent=2))

    if current_key == last_key:
        return

    major, minor, patch = latest.split(".")
    notes_url = f"https://code.visualstudio.com/updates/v{major}_{minor}"

    subject = f"VS Code stable {latest} [{status}]"

    body = f"""VS Code stable detected: {latest}

Status: {status}

Sources:
- update API: {update_v}
- GitHub release: {github_v}
- downloadable installer: {download_v}

Links:
- GitHub release: {github_url or "N/A"}
- Release notes: {notes_url}
- Windows User Installer endpoint:
  {DOWNLOAD_LATEST}

Download probe:
{download_probe}

Errors:
{chr(10).join(errors) if errors else "None"}

Action:
Start diffing and auditing VS Code {latest}.
"""

    send_mail(subject, body)

    save_state({
        "last_key": current_key,
        "last_version": latest,
        "last_status": status,
        "last_sent_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "update_api": update_v,
            "github": github_v,
            "download": download_v,
        }
    })


if __name__ == "__main__":
    main()

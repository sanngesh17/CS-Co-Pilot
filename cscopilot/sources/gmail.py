"""Gmail: inbox threads with new mail since `since` where the latest message isn't from you.

Setup: Google Cloud Console → enable Gmail API → OAuth client ID (Desktop app) → save the JSON as
.secrets/google_client_secret.json. The first fetch opens a browser to sign in; the token is cached.
Scope is read-only.
"""
import base64
import re
from datetime import datetime, timedelta
from email.utils import parseaddr

from ..config import SECRETS, gmail_secret
from ..models import Item

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN = SECRETS / "gmail_token.json"


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(str(gmail_secret()), SCOPES).run_local_server(port=0)
        SECRETS.mkdir(exist_ok=True)
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _body(payload: dict) -> str:
    """First text/plain part, with quoted history removed."""
    parts = [payload]
    while parts:
        p = parts.pop(0)
        if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data"):
            text = base64.urlsafe_b64decode(p["body"]["data"]).decode("utf-8", "replace")
            text = re.split(r"\r?\n\s*On .{5,200}wrote:\s*\r?\n", text)[0]
            return "\n".join(l for l in text.splitlines() if not l.startswith(">")).strip()
        parts += p.get("parts", [])
    return ""


def fetch(since: datetime, max_threads: int = 50) -> tuple[list[Item], str]:
    """Returns (items, my_email)."""
    gm = _service()
    me = gm.users().getProfile(userId="me").execute()["emailAddress"].lower()
    after = int((since - timedelta(minutes=1)).timestamp())
    q = f"in:inbox after:{after} -category:promotions -category:social -category:forums"
    threads = gm.users().threads().list(userId="me", q=q, maxResults=max_threads).execute().get("threads", [])

    items = []
    for t in threads:
        th = gm.users().threads().get(userId="me", id=t["id"], format="full").execute()
        msgs = []
        for m in th["messages"]:
            h = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
            name, addr = parseaddr(h.get("from", ""))
            msgs.append({"name": name or addr, "email": addr.lower(), "subject": h.get("subject", ""),
                         "automated": "list-unsubscribe" in h or bool(re.match(r"(no-?reply|notifications?|newsletter)@", addr.lower())),
                         "ts": datetime.fromtimestamp(int(m["internalDate"]) / 1000).astimezone(),
                         "text": _body(m["payload"]) or m.get("snippet", "")})
        latest = msgs[-1]
        if latest["email"] == me or latest["ts"] < since:
            continue                                    # you already replied, or nothing new
        items.append(Item(
            id=f"gmail-{t['id']}", source="gmail", from_name=latest["name"], from_email=latest["email"],
            received=latest["ts"], subject=msgs[0]["subject"], where="Email", automated=latest["automated"],
            thread=[{"from": "You" if x["email"] == me else x["name"], "text": x["text"][:4000]} for x in msgs[-5:]],
            url=f"https://mail.google.com/mail/u/0/#inbox/{t['id']}"))
    return items, me

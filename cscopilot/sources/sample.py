"""Sample personal inbox (emails + Slack DMs), timestamped relative to now."""
import json
from datetime import datetime, timedelta

from ..config import DATA
from ..models import Item


def fetch(since: datetime) -> list[Item]:
    now = datetime.now().astimezone()
    items = []
    for r in json.loads((DATA / "sample_inbox.json").read_text(encoding="utf-8")):
        received = now - timedelta(minutes=r["minutes_ago"])
        if received < since:
            continue
        items.append(Item(id=r["id"], source=r["source"], from_name=r["from_name"], from_email=r["from_email"],
                          received=received, thread=r["thread"], subject=r.get("subject", ""),
                          where=r.get("where", "Email"), automated=r.get("automated", False),
                          url="https://mail.google.com" if r["source"] == "gmail" else "https://slack.com"))
    return items

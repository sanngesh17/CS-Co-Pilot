"""Slack: DMs, group DMs and channels (including Slack Connect) with new messages since `since`.

DMs / group DMs : one item per conversation, if the other person spoke last.
Channels        : one item per thread with new replies (unless you replied last), and one per new top-level
                  message, with the 3 messages before it as context. Bots, joins and your own posts are skipped.

Setup: create a Slack app from slack_app_manifest.yaml, install it, and put the User OAuth Token (xoxp-…)
in .env as SLACK_USER_TOKEN. Read-only scopes. Exclude noisy channels with SLACK_EXCLUDE_CHANNELS=social,random
"""
import os
import re
from datetime import datetime, timedelta
from functools import lru_cache

from slack_sdk import WebClient

from ..models import Item

MAX_PER_CHANNEL = int(os.getenv("SLACK_MAX_PER_CHANNEL", "15"))


def _ts(value: str) -> datetime:
    return datetime.fromtimestamp(float(value)).astimezone()


def _human(m: dict) -> bool:
    return m.get("type") == "message" and not m.get("subtype") and not m.get("bot_id") and bool(m.get("user"))


class Slack:
    def __init__(self):
        self.client = WebClient(token=os.environ["SLACK_USER_TOKEN"])
        auth = self.client.auth_test()
        self.me, self.team, self.team_url = auth["user_id"], auth["team_id"], auth.get("url", "https://slack.com/")
        self.user = lru_cache(maxsize=None)(self._user)

    def _user(self, uid: str) -> dict:
        try:
            u = self.client.users_info(user=uid)["user"]
        except Exception:
            return {"name": uid, "email": "", "external": False, "bot": False}
        p = u.get("profile", {})
        return {"name": p.get("real_name") or u.get("real_name") or u.get("name") or uid,
                "email": (p.get("email") or "").lower(),
                "external": bool(u.get("team_id") and u["team_id"] != self.team),
                "bot": bool(u.get("is_bot"))}

    def clean(self, text: str) -> str:
        return re.sub(r"<@([A-Z0-9]+)(\|[^>]+)?>", lambda m: "@" + self.user(m.group(1))["name"], text or "")

    def _thread(self, msgs: list[dict]) -> list[dict]:
        return [{"from": "You" if m.get("user") == self.me else self.user(m["user"])["name"], "text": self.clean(m.get("text", ""))}
                for m in msgs]

    def _link(self, channel: str, ts: str) -> str:
        try:
            return self.client.chat_getPermalink(channel=channel, message_ts=ts)["permalink"]
        except Exception:
            return self.team_url

    def _conversations(self, types: str) -> list[dict]:
        out, cursor = [], None
        while True:
            r = self.client.users_conversations(types=types, exclude_archived=True, limit=200, cursor=cursor)
            out += r["channels"]
            cursor = r.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                return out

    def _item(self, kind: str, channel: str, where: str, last: dict, msgs: list[dict]) -> Item:
        sender = self.user(last["user"])
        return Item(id=f"slack-{channel}-{last['ts']}", source=kind, from_name=sender["name"], from_email=sender["email"],
                    received=_ts(last["ts"]), where=where, url=self._link(channel, last["ts"]), thread=self._thread(msgs))

    # ---------------------------------------------------------------- DMs + group DMs
    def dms(self, since: datetime) -> list[Item]:
        items = []
        for c in self._conversations("im,mpim"):
            if c.get("is_im") and c.get("user") in ("USLACKBOT",):
                continue
            hist = sorted(self.client.conversations_history(channel=c["id"], limit=20)["messages"], key=lambda m: float(m["ts"]))
            hist = [m for m in hist if _human(m)]
            new = [m for m in hist if m["user"] != self.me and _ts(m["ts"]) >= since]
            if not new or hist[-1]["user"] == self.me or self.user(new[-1]["user"])["bot"]:
                continue                                     # nothing new, or you replied last
            if c.get("is_mpim"):
                names = sorted({self.user(m["user"])["name"] for m in hist if m["user"] != self.me})
                where, kind = "Group DM: " + ", ".join(names[:4]), "slack_group_dm"
            else:
                where = "DM (Slack Connect)" if self.user(new[-1]["user"])["external"] else "DM"
                kind = "slack_dm"
            items.append(self._item(kind, c["id"], where, new[-1], hist[-6:]))
        return items

    # ---------------------------------------------------------------- channels
    def channels(self, since: datetime) -> list[Item]:
        exclude = {c.strip().lstrip("#").lower() for c in os.getenv("SLACK_EXCLUDE_CHANNELS", "").split(",") if c.strip()}
        items = []
        for c in self._conversations("public_channel,private_channel"):
            if c.get("name", "").lower() in exclude:
                continue
            where = f"#{c['name']}" + (" (Slack Connect)" if c.get("is_ext_shared") else "")
            oldest = str((since - timedelta(days=7)).timestamp())   # older parents can have new thread replies
            hist = sorted(self.client.conversations_history(channel=c["id"], oldest=oldest, limit=200)["messages"],
                          key=lambda m: float(m["ts"]))
            top = [m for m in hist if _human(m)]
            found = []
            for i, m in enumerate(top):
                if m.get("reply_count") and _ts(m.get("latest_reply", m["ts"])) >= since:
                    replies = [r for r in self.client.conversations_replies(channel=c["id"], ts=m["ts"], limit=100)["messages"]
                               if _human(r)]
                    new = [r for r in replies if r["user"] != self.me and _ts(r["ts"]) >= since]
                    if new and replies[-1]["user"] != self.me:
                        found.append(self._item("slack_channel", c["id"], where + " · thread", new[-1], replies[-8:]))
                elif not m.get("reply_count") and m["user"] != self.me and _ts(m["ts"]) >= since:
                    found.append(self._item("slack_channel", c["id"], where, m, top[max(0, i - 3): i + 1]))
            items += found[-MAX_PER_CHANNEL:]
        return items

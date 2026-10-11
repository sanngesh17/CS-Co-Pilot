"""Fetch → match account (Fixture) → prioritise → sort. Used by the Streamlit app."""
from datetime import datetime

from . import config
from .fixture import load_accounts, match_account
from .scorer import PRIORITY_ORDER, get_scorer

CHANNEL_SCOPES = "channels:history, channels:read, groups:history, groups:read"


def _slack_error(part: str, e: Exception) -> str:
    resp = getattr(e, "response", None)
    if resp is not None and resp.get("error") == "missing_scope":
        return (f"Slack {part}: the app needs more permissions. In api.slack.com/apps → CS Copilot → OAuth & Permissions, "
                f"add the User Token Scopes {CHANNEL_SCOPES}, click Reinstall, and copy the new xoxp- token into .env.")
    return f"Slack {part}: {type(e).__name__}: {e}"


def build_inbox(since: datetime, use_sample: bool, emails: bool = True, dms: bool = True, channels: bool = False) -> dict:
    st = config.settings()
    accounts, accounts_source = load_accounts()
    items, notes, sources = [], [], []

    if use_sample:
        from .sources import sample
        items = [i for i in sample.fetch(since) if (emails and i.is_email) or (dms and not i.is_email)]
        sources.append("Sample data")
    else:
        if emails and config.status()["gmail"]:
            try:
                from .sources import gmail
                found, my_email = gmail.fetch(since)
                st.me_email = st.me_email or my_email
                items += found
                sources.append("Gmail")
            except Exception as e:
                notes.append(f"Gmail: {type(e).__name__}: {e}")
        if (dms or channels) and config.status()["slack"]:
            try:
                from .sources.slack import Slack
                slack = Slack()
            except Exception as e:
                slack = None
                notes.append(_slack_error("connection", e))
            if slack and dms:
                try:
                    items += slack.dms(since)
                    sources.append("Slack DMs")
                except Exception as e:
                    notes.append(_slack_error("DMs", e))
            if slack and channels:
                try:
                    items += slack.channels(since)
                    sources.append("Slack channels")
                except Exception as e:
                    notes.append(_slack_error("channels", e))

    for it in items:
        it.account = match_account(it, accounts, st.company_domains)

    scorer = get_scorer(st.openai_model, st.me_name)
    now = datetime.now().astimezone()
    notes += [f"AI scoring failed, used keyword rules for {e}" for e in scorer.score_all(items, now)]
    # High → Medium → Low; inside each, the AI's 0-100 rank (not shown) then oldest first
    items.sort(key=lambda i: (PRIORITY_ORDER[i.score["priority"]], -i.score["score"], i.received))
    return {"items": items, "accounts": accounts, "accounts_source": accounts_source, "scorer": scorer.label,
            "sources": sources, "notes": notes, "since": since, "fetched_at": now, "me": st.me_name}

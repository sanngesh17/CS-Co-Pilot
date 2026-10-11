"""CS Copilot - one prioritised inbox for Customer Success.

Gmail + Slack DMs (personal messages first; channels later), scored by OpenAI from the message content and the
customer's account details in Fixture, highest first.
Run: double-click "Open CS Copilot UI.bat", or `streamlit run app.py`.
"""
import json
from datetime import datetime, timedelta

import streamlit as st

from cscopilot import config
from cscopilot.fixture import days_to_renewal, find_contact, llm_context, open_tickets
from cscopilot.inbox import build_inbox

st.set_page_config(page_title="CS Copilot", page_icon="📥", layout="wide")

DONE_FILE = config.DATA / ".done.json"
PRIORITY = {"high": ("High", "red"), "medium": ("Medium", "orange"), "low": ("Low", "gray")}
SOURCE = {"gmail": "Email", "slack_dm": "Slack DM", "slack_group_dm": "Slack group DM", "slack_channel": "Slack channel"}
HEALTH = {"good": "green", "watch": "orange", "at_risk": "red"}


# ------------------------------------------------------------------ helpers
def load_done() -> set:
    try:
        return set(json.loads(DONE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_done(done: set):
    DONE_FILE.parent.mkdir(exist_ok=True)
    DONE_FILE.write_text(json.dumps(sorted(done)), encoding="utf-8")


def away_start(choice: str) -> datetime:
    now = datetime.now().astimezone()
    if choice == "Since yesterday 6 pm":
        return (now - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    hours = {"Last 4 hours": 4, "Last 8 hours": 8, "Last 24 hours": 24, "Last 3 days": 72, "Last 7 days": 168}[choice]
    return now - timedelta(hours=hours)


def ago(ts: datetime) -> str:
    mins = int((datetime.now().astimezone() - ts).total_seconds() // 60)
    if mins < 60:
        return f"{mins}m ago"
    if mins < 1440:
        return f"{mins // 60}h {mins % 60}m ago"
    return f"{mins // 1440}d ago"


def account_badges(acct: dict | None) -> str:
    if not acct:
        return ""
    parts = [f":violet-badge[{acct['name']}]"]
    if acct.get("arr_gbp"):
        parts.append(f":gray-badge[£{acct['arr_gbp'] / 1000:.0f}k ARR]")
    d = days_to_renewal(acct)
    if d is not None:
        parts.append(f":{'orange' if 0 <= d <= 60 else 'gray'}-badge[renews in {d}d]")
    if acct.get("health"):
        parts.append(f":{HEALTH.get(acct['health'], 'gray')}-badge[{acct['health'].replace('_', ' ')}]")
    return " ".join(parts)


def render_item(it, done: set):
    s = it.score
    label, color = PRIORITY[s["priority"]]
    contact = find_contact(it, it.account)
    with st.container(border=True):
        who = f"**{contact['name']}** · {contact.get('role', '')}" if contact else f"**{it.from_name}**"
        title = who + (f" · {it.subject}" if it.subject else "")
        where = SOURCE.get(it.source, it.source) + (f" · {it.where}" if it.source == "slack_channel" else "")
        st.markdown(f"{title}  \n:gray[{where} · {ago(it.received)}]")
        st.markdown(f":{color}-badge[{label} priority] {account_badges(it.account)}")
        st.markdown(s["summary"] or it.latest_text[:200])
        if s.get("why"):
            st.caption(s["why"])
        with st.expander("Conversation"):
            for m in it.thread:
                st.markdown(f"**{m['from']}:** {m['text']}")
        if s.get("suggested_reply"):
            st.code(s["suggested_reply"], language=None, wrap_lines=True)
        ctx = llm_context(it, it.account)
        if ctx:
            with st.expander("CRM context sent to the AI"):
                st.json(ctx, expanded=1)
        a, b, _ = st.columns([2, 2, 6])
        if it.url:
            a.link_button("Open in Gmail" if it.is_email else "Open in Slack", it.url)
        b.checkbox("Done", value=it.id in done, key=f"done-{it.id}")


# ------------------------------------------------------------------ sidebar
status = config.status()
with st.sidebar:
    st.title("CS Copilot")
    away = st.selectbox("Show messages from", ["Last 4 hours", "Last 8 hours", "Since yesterday 6 pm",
                                               "Last 24 hours", "Last 3 days", "Last 7 days"], index=3)
    inc_email = st.toggle("Gmail" + ("" if status["gmail"] else " (not connected)"), value=True)
    inc_dms = st.toggle("Slack DMs" + ("" if status["slack"] else " (not connected)"), value=True)
    inc_channels = st.toggle("Slack channels" + ("" if status["slack"] else " (not connected)"), value=True)
    hide_done = st.toggle("Hide done", value=True)
    refresh = st.button("Refresh", type="primary", width="stretch")

# ------------------------------------------------------------------ data
params = (away, inc_email, inc_dms, inc_channels)
if refresh or st.session_state.get("params") != params:
    with st.spinner("Loading…"):
        st.session_state.inbox = build_inbox(away_start(away), False, inc_email, inc_dms, inc_channels)
        st.session_state.params = params
inbox = st.session_state.inbox
done = load_done()
for _it in inbox["items"]:                      # apply Done ticks from this click before counting
    ticked = st.session_state.get(f"done-{_it.id}")
    if ticked is True:
        done.add(_it.id)
    elif ticked is False:
        done.discard(_it.id)

# ------------------------------------------------------------------ main
tab_inbox, tab_accounts, tab_how = st.tabs(["Inbox", "Accounts", "Architecture"])

with tab_inbox:
    items = inbox["items"]
    open_items = [i for i in items if i.id not in done]
    n = len(items)
    st.caption(f"{n} conversation{'s' if n != 1 else ''} since {inbox['since'].strftime('%a %H:%M')} · "
               f"{' + '.join(inbox['sources']) or 'no sources'} · updated {inbox['fetched_at'].strftime('%H:%M')}")
    if not inbox["sources"]:
        st.warning("No sources connected. Add your Slack token or Gmail file to .env (see README).")
    for note in inbox["notes"]:
        st.warning(note)

    counts = {p: sum(1 for i in open_items if i.score["priority"] == p) for p in PRIORITY}
    m = st.columns(4)
    for col, (p, (label, _)) in zip(m, PRIORITY.items()):
        col.metric(f"{label} priority", counts[p])
    m[3].metric("Done", sum(1 for i in items if i.id in done))

    show = st.pills("Show", [PRIORITY[p][0] for p in PRIORITY], selection_mode="multi",
                    default=[PRIORITY[p][0] for p in PRIORITY], label_visibility="collapsed")
    wanted = {p for p in PRIORITY if PRIORITY[p][0] in (show or [])}
    visible = [i for i in items if i.score["priority"] in wanted and not (hide_done and i.id in done)]
    if not visible:
        st.caption("No messages.")
    for it in visible:
        render_item(it, done)
    save_done(done)

with tab_accounts:
    waiting = {}
    for i in inbox["items"]:
        if i.account and i.id not in done:
            waiting[i.account["name"]] = waiting.get(i.account["name"], 0) + 1
    rows = [{"Account": a["name"], "Segment": a.get("segment", ""), "ARR (£)": a.get("arr_gbp"),
             "Renewal": a.get("renewal_date"), "Days to renewal": days_to_renewal(a),
             "Health": a.get("health", "").replace("_", " "), "Stage": a.get("stage", ""),
             "Open tickets": len(open_tickets(a)), "Waiting": waiting.get(a["name"], 0),
             "Health reason": a.get("health_reason", "")} for a in inbox["accounts"]]
    st.dataframe(sorted(rows, key=lambda r: (r["Days to renewal"] if r["Days to renewal"] is not None else 9999)),
                 hide_index=True, width="stretch")
    for a in inbox["accounts"]:
        with st.expander(f"{a['name']}: full CRM record"):
            st.json(a, expanded=1)

with tab_how:
    st.image(str(config.ROOT / "architecture.svg"), width="stretch")

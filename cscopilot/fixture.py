"""Account details from Fixture (CRM), and the slice of them that goes to the LLM.

Uses the Fixture API when FIXTURE_API_KEY + FIXTURE_ACCOUNTS_URL are set; otherwise data/fixture_accounts.json,
which has the same shape. Fields (all optional except name):

  name, domains[], segment, plan, arr_gbp, contract_start, renewal_date, health, health_reason, stage
  team {csm, ae, se}
  contacts[] {name, role, email, notes}
  contract {sla_first_response, entitlements[], pricing_rule}
  usage {seats_active, seats_total, adoption_pct, obligations_mapped, controls_linked, top_features[], last_qbr}
  deals[] {name, stage, amount_gbp, close_date, next_step, owner}
  tickets[] {id, title, status, priority, eta}
  commitments[] {what, owner, due, status}
  recent_activity[] {date, type, summary}
  risks[], talking_points[], notes
"""
import json
import os
import re
from datetime import date

import requests

from .config import DATA
from .models import Item

PUBLIC_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com"}
CLOSED = {"resolved", "closed", "done", "cancelled"}


def load_accounts() -> tuple[list[dict], str]:
    """Returns (accounts, source_label)."""
    if os.getenv("FIXTURE_API_KEY") and os.getenv("FIXTURE_ACCOUNTS_URL"):
        try:
            return _from_api(), "Fixture API"
        except Exception as e:  # keep the inbox usable if the CRM is down
            print(f"[fixture] API failed, using local file: {e}")
    return json.loads((DATA / "fixture_accounts.json").read_text(encoding="utf-8")), "Local file"


def _from_api() -> list[dict]:
    r = requests.get(os.environ["FIXTURE_ACCOUNTS_URL"],
                     headers={"Authorization": f"Bearer {os.environ['FIXTURE_API_KEY']}"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data.get("data", data) if isinstance(data, dict) else data


def days_to_renewal(account: dict) -> int | None:
    try:
        return (date.fromisoformat(account["renewal_date"]) - date.today()).days
    except (KeyError, ValueError, TypeError):
        return None


def open_tickets(account: dict) -> list[dict]:
    return [t for t in account.get("tickets", []) if str(t.get("status", "")).lower() not in CLOSED]


def _contact_emails(account: dict) -> list[str]:
    return [e.lower() for e in account.get("contact_emails", [])] + \
           [c["email"].lower() for c in account.get("contacts", []) if c.get("email")]


def match_account(item: Item, accounts: list[dict], company_domains: list[str]) -> dict | None:
    """Known contact email, then company domain; for internal senders, the channel or an account named in the text."""
    email = item.from_email.lower()
    for a in accounts:
        if email and email in _contact_emails(a):
            return a
    domain = email.split("@")[-1] if "@" in email else ""
    if domain and domain not in company_domains and domain not in PUBLIC_DOMAINS:
        for a in accounts:
            if domain in [d.lower() for d in a.get("domains", [])]:
                return a
    text = " ".join(m["text"] for m in item.thread) + " " + item.subject + " " + item.where.replace("-", " ")
    for a in accounts:
        first = a["name"].split()[0]
        if (re.search(rf"\b{re.escape(a['name'])}\b", text, re.I) or re.search(rf"\b{re.escape(first)}\b", text)
                or re.search(rf"#{re.escape(first)}\b", text, re.I)):
            return a
    return None


def find_contact(item: Item, account: dict | None) -> dict | None:
    """The CRM contact who sent the message (by email, then by name)."""
    if not account:
        return None
    email = item.from_email.lower()
    for c in account.get("contacts", []):
        if email and c.get("email", "").lower() == email:
            return c
    for c in account.get("contacts", []):
        if c.get("name") and c["name"].split()[0].lower() in item.from_name.lower():
            return c
    return None


def llm_context(item: Item, account: dict | None) -> dict | None:
    """What the LLM gets from the CRM: enough to judge priority and draft a specific reply, nothing it can't use."""
    if not account:
        return None
    sender = find_contact(item, account)
    ctx = {
        "account": account["name"],
        "segment": account.get("segment"),
        "plan": account.get("plan"),
        "arr_gbp": account.get("arr_gbp"),
        "renewal_date": account.get("renewal_date"),
        "days_to_renewal": days_to_renewal(account),
        "health": account.get("health"),
        "health_reason": account.get("health_reason"),
        "stage": account.get("stage"),
        "sender_in_crm": sender,                                   # real name, role, how they like to be updated
        "our_team": account.get("team"),
        "contract": account.get("contract"),                       # SLA, entitlements, pricing rule
        "usage": account.get("usage"),
        "open_deals": account.get("deals"),
        "open_tickets": open_tickets(account),
        "recently_resolved_tickets": [t for t in account.get("tickets", []) if t not in open_tickets(account)][:3],
        "open_commitments": [c for c in account.get("commitments", []) if str(c.get("status", "")).lower() != "done"],
        "recent_activity": account.get("recent_activity", [])[:3],
        "risks": account.get("risks"),
        "talking_points": account.get("talking_points"),
        "notes": account.get("notes") or None,
    }
    return {k: v for k, v in ctx.items() if v not in (None, [], {}, "")}

"""Score each message 0-100 from its CONTENT, adjusted by the Fixture ACCOUNT context.

- OpenAIScorer : one Chat Completions call per message, JSON output, cached on disk so a message is only
                 scored (and billed) once.
- RuleScorer   : keyword fallback when there's no OPENAI_API_KEY - same output shape.
"""
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .config import DATA
from .fixture import days_to_renewal, find_contact, llm_context
from .models import Item

CACHE = DATA / ".score_cache.json"
PRIORITIES = [(70, "high"), (40, "medium"), (0, "low")]
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
CATEGORIES = ["escalation", "security", "churn_risk", "renewal", "bug", "request", "question",
              "commitment", "internal", "positive", "social", "automated"]

SYSTEM = """You prioritise the inbox of {me}, a Customer Success Manager at Cardamon (an AI regulatory-compliance
platform for regulated financial firms). {me} has been away and wants to know what to answer first.

Score each message 0-100 for how important it is that {me} responds, then explain briefly.
Judge mainly by the message CONTENT: what is being asked, of whom, what breaks if it waits, and any deadlines.
Then use the ACCOUNT context from the CRM (Fixture) to adjust: add up to +10 when the message affects the
relationship AND the account is renewing within 60 days, has health "watch"/"at_risk", or has high ARR.
Never raise social, thank-you or automated messages because of the account. The channel (email vs Slack DM)
is not a reason on its own.

The score only orders messages; {me} sees it as a priority:
70-100 HIGH: customer blocked, security/data incident, churn threat or escalation, a promise due today,
       a direct ask of {me} from a customer, renewal or deal work, a deadline within days.
40-69 MEDIUM: questions or requests with no near deadline; colleagues needing input.
0-39 LOW: thanks, social, newsletters, automated mail, general channel chatter not aimed at {me}, already resolved.
For channel messages, ask whether {me} specifically needs to act; most channel traffic is MEDIUM or LOW.

Drafting the reply with the CRM:
- Greet the sender by the first name in sender_in_crm (the Slack/email display name may be wrong) and follow
  their notes (e.g. "prefers short updates with dates").
- Use the CRM facts that answer or move the conversation forward: the open deal's stage and next step, ticket
  status and ETA, commitments {me} owes and their due dates, usage numbers, entitlements, talking points when
  they ask about a competitor. Use only the one or two facts that matter most for THIS message; not a data dump.
- Answer what they asked first, then the fact, then a concrete next step.
- If {me} owes them something in open_commitments, acknowledge it with its due date.
- Follow contract.pricing_rule: never quote prices or discounts; say the AE will follow up (name them from our_team).
- Never promise a date the CRM doesn't confirm (e.g. a ticket ETA that says "no confirmed date").
- Never invent facts that are not in the message or the CRM. If something is unknown, say you'll check.
- If there is no CRM context, write a short, neutral reply.

Writing style for every text field: plain and specific, like a colleague's note. No filler, no pleasantries,
no em dashes, no exclamation marks, no phrases like "Of course", "Happy to help", "I hope this finds you well",
"Great question" or "Just checking in".

Return ONLY a JSON object:
{{"score": <0-100 integer>,
  "category": one of {categories},
  "summary": "<what they need, max 12 words, e.g. 'Wants an update on the Microsoft deal'>",
  "why": "<max 15 words, the facts behind the priority, e.g. 'Direct ask, no deadline given' or
          'Competitor comparison during a £120k expansion deal'>",
  "needs_reply": <true|false>,
  "suggested_reply": "<what {me} would actually type: 2-3 short sentences for Slack, 3-5 for email, British
   spelling, no sign-off on Slack. Empty string if no reply is needed.>"}}"""
PROMPT_VERSION = "6"   # bump when the prompt changes so cached scores are refreshed


def priority(score: int) -> str:
    return next(p for threshold, p in PRIORITIES if score >= threshold)


def _account_text(item: Item) -> str:
    ctx = llm_context(item, item.account)
    if not ctx:
        return "No matching account in Fixture (internal colleague or unknown sender)."
    return json.dumps(ctx, ensure_ascii=False, indent=1)


def _message_text(item: Item, now: datetime) -> str:
    mins = int((now - item.received).total_seconds() // 60)
    head = [f"Channel: {'Email' if item.is_email else 'Slack ' + item.where}",
            f"From: {item.from_name} <{item.from_email}>", f"Received: {mins} minutes ago"]
    if item.subject:
        head.append(f"Subject: {item.subject}")
    if item.automated:
        head.append("Sender looks automated (newsletter / no-reply).")
    convo = "\n".join(f"{m['from']}: {m['text']}" for m in item.thread)
    return "\n".join(head) + f"\n\nConversation (oldest first):\n{convo}"


def _normalise(raw: dict) -> dict:
    score = max(0, min(100, int(raw.get("score", 0))))
    cat = raw.get("category") if raw.get("category") in CATEGORIES else "request"
    return {"score": score, "priority": priority(score), "category": cat,
            "summary": str(raw.get("summary", ""))[:400], "why": str(raw.get("why", ""))[:400],
            "needs_reply": bool(raw.get("needs_reply", score >= 40)),
            "suggested_reply": str(raw.get("suggested_reply", "")).strip()}


class _Cache:
    def __init__(self):
        try:
            self.data = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    @staticmethod
    def key(model: str, item: Item) -> str:
        blob = json.dumps([PROMPT_VERSION, model, item.id, item.thread, item.subject, _account_text(item)],
                          ensure_ascii=False)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()

    def save(self):
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(self.data), encoding="utf-8")


class OpenAIScorer:
    def __init__(self, model: str, me: str):
        from openai import OpenAI

        self.client = OpenAI()                          # reads OPENAI_API_KEY
        self.model, self.me = model, me
        self.system = SYSTEM.format(me=me, categories=CATEGORIES)
        self.label = f"OpenAI {model}"

    def _one(self, item: Item, now: datetime) -> dict:
        prompt = f"MESSAGE\n{_message_text(item, now)}\n\nCRM CONTEXT (from Fixture)\n{_account_text(item)}"
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": self.system}, {"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return _normalise(json.loads(resp.choices[0].message.content or "{}"))

    def score_all(self, items: list[Item], now: datetime) -> list[str]:
        """Scores in place. Returns a list of error strings (items that fell back to rules)."""
        cache, todo, errors = _Cache(), [], []
        for it in items:
            hit = cache.data.get(_Cache.key(self.model, it))
            if hit:
                it.score = hit
            else:
                todo.append(it)

        def work(it: Item):
            try:
                return it, self._one(it, now), None
            except Exception as e:                      # one bad call shouldn't break the inbox
                return it, RuleScorer(self.me).score(it, now), f"{it.from_name}: {type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=int(os.getenv("OPENAI_PARALLEL", "6"))) as pool:
            for it, sc, err in pool.map(work, todo):
                it.score = sc
                if err:
                    errors.append(err)
                else:
                    cache.data[_Cache.key(self.model, it)] = sc
        cache.save()
        return errors


class RuleScorer:
    """No-API fallback: keyword rules on the content + the same account adjustment."""
    label = "Rules (no OpenAI key)"

    RULES = [  # (pattern, base score, category)
        (r"unauthori[sz]ed|breach|audit log|data protection|\bDPO\b", 92, "security"),
        (r"another vendor|competitor|cheaper|lower price|go to market|cancel", 88, "churn_risk"),
        (r"\bblocked\b|outage|not working|urgent|asap|as soon as possible", 86, "escalation"),
        (r"told them you'd|you'd send|i'll .{0,40}(by|back)|by end of day|\bEOD\b", 84, "commitment"),
        (r"any update", 80, "request"),
        (r"let .{0,20}know|passed QA|is live|deployed", 70, "internal"),
        (r"renewal|procurement|usage summary|expansion|proposal|adoption", 68, "renewal"),
        (r"\blunch\b|\bcoffee\b|\bdrinks\b|🍜|🍕", 10, "social"),
        (r"can you|could you|please|\?", 55, "question"),
        (r"thanks|thank you|really useful|great session", 20, "positive"),
    ]

    def __init__(self, me: str = "you"):
        self.me = me

    def score(self, item: Item, now: datetime) -> dict:
        text = item.subject + "\n" + "\n".join(m["text"] for m in item.thread if m["from"] != "You")
        score, cat = 45, "request"
        for pat, base, c in self.RULES:
            if re.search(pat, text, re.I):
                score, cat = base, c
                break
        if item.automated:
            score, cat = 5, "automated"
        if re.search(r"no rush|no hurry|next quarter|when you get a chance", text, re.I):
            score = min(score, 50)
        if item.source == "slack_channel" and not re.search(rf"@?{re.escape(self.me)}\b", text, re.I):
            score = min(score, 55)                      # channel chatter not aimed at you
        why, acct = f"Keyword match: {cat.replace('_', ' ')}.", item.account
        if acct and cat not in ("social", "positive", "automated"):
            d, bump = days_to_renewal(acct), 0
            if d is not None and 0 <= d <= 60:
                bump += 5
            if acct.get("health") in ("watch", "at_risk"):
                bump += 5
            if bump:
                score = min(100, score + bump)
                why += f" +{bump}: {acct['name']} renews in {d} days, health {acct.get('health', '').replace('_', ' ')}."
        body = re.sub(r"^\s*(hi|hello|hey|dear)\b[^,\n]{0,30},?\s*", "", item.latest_text.strip(), flags=re.I)
        first = re.split(r"(?<=[.!?])\s", body.replace("\n", " ").strip())[0][:200]
        contact = find_contact(item, acct)
        name = (contact or {}).get("name") or item.from_name
        reply = "" if score < 40 else f"Hi {name.split()[0]}, looking into this now. I'll come back to you today."
        return {"score": score, "priority": priority(score), "category": cat, "summary": first, "why": why,
                "needs_reply": score >= 40, "suggested_reply": reply}

    def score_all(self, items: list[Item], now: datetime) -> list[str]:
        for it in items:
            it.score = self.score(it, now)
        return []


def get_scorer(model: str, me: str):
    return OpenAIScorer(model, me) if os.getenv("OPENAI_API_KEY") else RuleScorer(me)

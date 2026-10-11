"""Shared data shapes."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Item:
    """One thing waiting for you: an email thread or a Slack DM conversation."""
    id: str
    source: str                         # "gmail" | "slack_dm" | "slack_group_dm" | "slack_channel"
    from_name: str
    from_email: str
    received: datetime                  # time of the latest message from someone else
    thread: list[dict]                  # [{"from": str, "text": str}], oldest first
    subject: str = ""
    where: str = ""                     # "DM", "DM (Slack Connect)", "Group DM: …"
    url: str = ""
    automated: bool = False             # newsletters / no-reply senders
    account: Optional[dict] = None      # matched Fixture account
    score: Optional[dict] = None        # filled by the scorer

    @property
    def latest_text(self) -> str:
        return self.thread[-1]["text"] if self.thread else ""

    @property
    def is_email(self) -> bool:
        return self.source == "gmail"


@dataclass
class Settings:
    me_name: str = "Sanngesh"
    me_email: str = ""
    company_domains: list[str] = field(default_factory=lambda: ["cardamon.ai"])
    openai_model: str = "gpt-6-luna"

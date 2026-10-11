"""Paths, .env loading and connection status."""
import os
from pathlib import Path

from dotenv import load_dotenv

from .models import Settings

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SECRETS = ROOT / ".secrets"

load_dotenv(ROOT / ".env")


def settings() -> Settings:
    return Settings(
        me_name=os.getenv("CSM_NAME", "Sanngesh"),
        me_email=os.getenv("CSM_EMAIL", ""),
        company_domains=[d.strip().lower() for d in os.getenv("COMPANY_DOMAINS", "cardamon.ai").split(",") if d.strip()],
        openai_model=os.getenv("OPENAI_MODEL", "gpt-6-luna"),
    )


def gmail_secret() -> Path:
    return Path(os.getenv("GOOGLE_CLIENT_SECRET_FILE", SECRETS / "google_client_secret.json"))


def status() -> dict:
    return {
        "gmail": gmail_secret().exists(),
        "slack": bool(os.getenv("SLACK_USER_TOKEN")),
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "fixture_api": bool(os.getenv("FIXTURE_API_KEY") and os.getenv("FIXTURE_ACCOUNTS_URL")),
    }

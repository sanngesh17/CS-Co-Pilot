"""Paths, settings and connection status.

Keys come from .env locally, or from Streamlit secrets when deployed (Streamlit Community Cloud → App settings → Secrets).
"""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .models import Settings

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SECRETS = ROOT / ".secrets"

load_dotenv(ROOT / ".env")
try:                                    # deployed: copy Streamlit secrets into the environment
    import streamlit as st

    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float)):
            os.environ.setdefault(_k, str(_v))
except Exception:                       # no secrets file locally - that's fine
    pass

LOCAL_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Europe/London"))


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

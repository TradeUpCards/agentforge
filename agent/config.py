"""Configuration loading from environment / .env file.

Centralized so callers don't reach into os.environ directly. All env reads
happen here; the rest of the app uses the typed `settings` object.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel

# Load .env from this file's directory so it works regardless of cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, ".env"))


class Settings(BaseModel):
    # LLM provider
    anthropic_api_key: str
    anthropic_base_url: str
    model_reasoning: str
    model_workhorse: str

    # Observability
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str

    # DB
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str

    # Auth
    openemr_hmac_secret: str

    # Service
    host: str
    port: int
    log_level: str

    # Dev mode
    use_fixture_llm: bool

    @property
    def has_real_llm_key(self) -> bool:
        """True if a non-placeholder Anthropic key is present."""
        key = self.anthropic_api_key.strip()
        if not key:
            return False
        # Filter out template placeholders
        return not key.startswith("<") and not key.endswith(">")


def _resolve_use_fixture_llm(raw: str, has_real_key: bool) -> bool:
    """USE_FIXTURE_LLM=auto means: fixtures iff no real key is present."""
    raw = raw.strip().lower()
    if raw == "auto" or raw == "":
        return not has_real_key
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(
        f"USE_FIXTURE_LLM must be one of: auto, true, false (got {raw!r})"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    has_real_key = bool(api_key.strip()) and not api_key.strip().startswith("<")

    return Settings(
        anthropic_api_key=api_key,
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        model_reasoning=os.getenv("ANTHROPIC_MODEL_REASONING", "claude-sonnet-4-6"),
        model_workhorse=os.getenv("ANTHROPIC_MODEL_WORKHORSE", "claude-haiku-4-5"),
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip('"'),
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip('"'),
        langfuse_host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip('"'),
        db_host=os.getenv("AGENT_DB_HOST", "localhost"),
        db_port=int(os.getenv("AGENT_DB_PORT", "8320")),
        db_user=os.getenv("AGENT_DB_USER", "openemr"),
        db_password=os.getenv("AGENT_DB_PASS", "openemr"),
        db_name=os.getenv("AGENT_DB_NAME", "openemr"),
        openemr_hmac_secret=os.getenv("OPENEMR_HMAC_SECRET", ""),
        host=os.getenv("AGENT_HOST", "0.0.0.0"),
        port=int(os.getenv("AGENT_PORT", "8000")),
        log_level=os.getenv("AGENT_LOG_LEVEL", "info"),
        use_fixture_llm=_resolve_use_fixture_llm(
            os.getenv("USE_FIXTURE_LLM", "auto"),
            has_real_key,
        ),
    )

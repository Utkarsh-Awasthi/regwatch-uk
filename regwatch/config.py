"""Runtime settings, all overridable through environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("REGWATCH_DB", ROOT / "data" / "regwatch.db")))
    outbox_dir: Path = field(default_factory=lambda: Path(os.getenv("REGWATCH_OUTBOX", ROOT / "data" / "outbox")))
    base_url: str = os.getenv("REGWATCH_BASE_URL", "http://localhost:8000")
    # Polite crawling: identify ourselves and rate-limit.
    user_agent: str = os.getenv("REGWATCH_USER_AGENT", "RegWatchUK/0.1 (+https://github.com/regwatch-uk)")
    request_delay_s: float = float(os.getenv("REGWATCH_REQUEST_DELAY", "0.2"))
    # LLM summaries are optional; without a key we use the extractive summariser.
    llm_model: str = os.getenv("REGWATCH_LLM_MODEL", "claude-opus-5-5")
    llm_enabled: bool = os.getenv("REGWATCH_LLM", "auto") != "off"
    # Faithfulness gate: LLM summaries scoring below this fall back to extractive.
    min_faithfulness: float = float(os.getenv("REGWATCH_MIN_FAITHFULNESS", "0.9"))
    # SMTP is optional; without it digests are written to the outbox directory.
    smtp_host: str | None = os.getenv("REGWATCH_SMTP_HOST")
    smtp_port: int = int(os.getenv("REGWATCH_SMTP_PORT", "587"))
    smtp_user: str | None = os.getenv("REGWATCH_SMTP_USER")
    smtp_password: str | None = os.getenv("REGWATCH_SMTP_PASSWORD")
    mail_from: str = os.getenv("REGWATCH_MAIL_FROM", "digest@regwatch.local")
    # Secret for signing unsubscribe / feedback / click links.
    secret: str = os.getenv("REGWATCH_SECRET", "dev-secret-change-me")


settings = Settings()

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr


class Settings(BaseModel):
    agent_email: str = ""
    model_provider: Literal["vertex", "openai"] = "vertex"
    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    gemini_model: str = "gemini-3.8-flash"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-5.4-mini-2026-03-17"
    oauth_client_file: Path = Path("credentials/oauth-client.json")
    oauth_token_file: Path = Path("credentials/gmail-token.json")
    pubsub_topic: str = ""
    pubsub_subscription: str = ""
    data_dir: Path = Path("var")
    headless: bool = True
    browser_channel: str | None = None
    attachment_budget_bytes: int = Field(default=18_000_000, gt=0, le=20_000_000)
    max_message_bytes: int = Field(default=24_000_000, gt=0, le=25_000_000)
    reconciliation_seconds: int = Field(default=600, ge=60)

    @property
    def email_attachment_budget(self) -> int:
        # Base64 and MIME line wrapping expand attachments; reserve space for headers and text.
        return min(self.attachment_budget_bytes, self.max_message_bytes * 7 // 10)

    @classmethod
    def load(cls) -> Settings:
        load_dotenv(Path.cwd() / ".env")
        if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        return cls(
            agent_email=os.getenv("AGENT_EMAIL", ""),
            model_provider=os.getenv("MODEL_PROVIDER", "vertex"),
            google_cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
            google_cloud_location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            openai_api_key=SecretStr(os.getenv("OPENAI_API_KEY", "")),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini-2026-03-17"),
            oauth_client_file=Path(
                os.getenv("GOOGLE_OAUTH_CLIENT_FILE", "credentials/oauth-client.json")
            ),
            oauth_token_file=Path(
                os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "credentials/gmail-token.json")
            ),
            pubsub_topic=os.getenv("PUBSUB_TOPIC", ""),
            pubsub_subscription=os.getenv("PUBSUB_SUBSCRIPTION", ""),
            data_dir=Path(os.getenv("AGENT_DATA_DIR", "var")),
            headless=os.getenv("HEADLESS", "true").lower() not in {"0", "false", "no"},
            browser_channel=os.getenv("BROWSER_CHANNEL") or None,
            attachment_budget_bytes=int(os.getenv("ATTACHMENT_BUDGET_BYTES", "18000000")),
            max_message_bytes=int(os.getenv("MAX_MESSAGE_BYTES", "24000000")),
        )

    def require_worker(self) -> None:
        missing = [
            name
            for name, value in {
                "AGENT_EMAIL": self.agent_email,
                "PUBSUB_TOPIC": self.pubsub_topic,
                "PUBSUB_SUBSCRIPTION": self.pubsub_subscription,
            }.items()
            if not value
        ]
        if self.model_provider == "vertex":
            if not self.google_cloud_project:
                missing.append("GOOGLE_CLOUD_PROJECT")
        elif not self.openai_api_key.get_secret_value():
            missing.append("OPENAI_API_KEY")
        if missing:
            raise ValueError(f"Missing configuration: {', '.join(missing)}")
        if not self.oauth_token_file.is_file():
            raise ValueError("Gmail is not authorized yet; run the auth command first")

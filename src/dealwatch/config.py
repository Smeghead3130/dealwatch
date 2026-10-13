import re
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    data_dir: Path = Path("/data")
    tz: str = "Australia/Brisbane"
    poll_seconds: int = Field(120, ge=60)
    feed_url: str = "https://www.ozbargain.com.au/deals/feed"
    user_agent: str = "Mozilla/5.0 (compatible; Dealwatch/0.1; personal deal monitor)"
    ollama_url: str = "http://192.168.50.157:30069"
    llm_model: str = "qwen3.5:9b"
    fallback_model: str = "qwen3.5:4b"
    llm_timeout: int = Field(300, ge=1)
    ntfy_url: str = "http://192.168.50.157:30184"
    ntfy_topic: str = ""
    ntfy_token: SecretStr = SecretStr("")
    ntfy_username: SecretStr = SecretStr("")
    ntfy_password: SecretStr = SecretStr("")
    dry_run: bool = True
    quiet_hours: str = ""
    keep_email: str = ""
    keep_master_token: SecretStr = SecretStr("")
    keep_note_title: str = "Wishlist"
    keep_sync_minutes: int = Field(15, ge=1, le=1440)
    wishlist_history_ratio: float = Field(0.85, gt=0, lt=1)
    wishlist_quoted_ratio: float = Field(0.85, gt=0, lt=1)
    history_ratio: float = Field(0.70, gt=0, lt=1)
    quoted_ratio: float = Field(0.75, gt=0, lt=1)
    min_observations: int = Field(3, ge=3)
    history_days: int = Field(180, ge=1, le=365)
    possible_daily_limit: int = Field(5, ge=0)
    stale_hours: int = Field(6, ge=1)
    max_job_attempts: int = Field(3, ge=1, le=10)
    exclude_categories: str = (
        "Financial,Credit Card,Bank Account,Mobile Plans,Internet,Internet Plans,Gambling,Subscription"
    )
    log_level: str = "INFO"

    @field_validator("ollama_url")
    @classmethod
    def guard_proxy(cls, v):
        from urllib.parse import urlsplit

        if urlsplit(v).port == 30068:
            raise ValueError("Use gpu-guard on port 30069; direct port 30068 is forbidden")
        return v.rstrip("/")

    @field_validator("feed_url", "ollama_url", "ntfy_url")
    @classmethod
    def http_url(cls, v):
        from urllib.parse import urlsplit

        p = urlsplit(v)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            raise ValueError("Use an HTTP(S) URL without credentials")
        return v

    @field_validator("tz")
    @classmethod
    def timezone(cls, v):
        ZoneInfo(v)
        return v

    @field_validator("ntfy_topic")
    @classmethod
    def topic(cls, v):
        if v and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v):
            raise ValueError("Topic must contain only letters, digits, underscores or hyphens")
        return v

    @field_validator("quiet_hours")
    @classmethod
    def quiet(cls, v):
        if v and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("Use HH:MM-HH:MM")
        return v

    @model_validator(mode="after")
    def auth(self):
        if bool(self.ntfy_username.get_secret_value()) != bool(self.ntfy_password.get_secret_value()):
            raise ValueError("Set both NTFY_USERNAME and NTFY_PASSWORD")
        if self.ntfy_token.get_secret_value() and self.ntfy_username.get_secret_value():
            raise ValueError("Choose token or basic auth, not both")
        return self

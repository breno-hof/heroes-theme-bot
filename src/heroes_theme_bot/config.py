from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from HT_* environment variables (and .env in development)."""

    model_config = SettingsConfigDict(env_prefix="HT_", env_file=".env", extra="ignore")

    discord_token: SecretStr
    guild_id: int
    voice_channel_id: int

    ws_token: SecretStr = Field(min_length=16)
    ws_host: str = "0.0.0.0"  # noqa: S104 - the container must listen on all interfaces
    ws_port: int = Field(default=8765, ge=0, le=65535)  # 0 picks a free port (tests)
    ws_allowed_origins: list[str] | None = None
    ws_handshake_timeout_s: float = Field(default=5.0, gt=0)

    silence_timeout_ms: int = Field(default=300, ge=60, le=5000)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

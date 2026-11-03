"""Application configuration loaded from environment variables / .env.

All settings use the ``PPP_`` prefix so they do not collide with other tools.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PPP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="sqlite:///data/prizepicks.db",
        description="SQLAlchemy URL for the application database.",
    )
    balldontlie_api_key: str | None = Field(
        default=None,
        description="API key for BALLDONTLIE. Optional; only needed for the live adapter.",
    )
    balldontlie_base_url: str = "https://api.balldontlie.io/v1"
    calibration_dir: str = Field(
        default="artifacts/calibration",
        description="Directory of calibration artifacts applied by predict()",
    )
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()

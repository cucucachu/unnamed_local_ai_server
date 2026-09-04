"""Application settings, sourced from environment variables via pydantic-settings.

Variable names match `.env.example`, lower-cased and unprefixed — same
convention as `agent-server`'s/`code-exec-manager`'s own `app/core/config.py`.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    egress_proxy_url: str = "http://egress-proxy:8080"
    fetch_timeout_s: int = 20
    fetch_max_bytes: int = 5_000_000
    fetch_max_text_chars: int = 40_000
    fetch_max_redirects: int = 5

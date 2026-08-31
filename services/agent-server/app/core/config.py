"""Application settings, sourced from environment variables via pydantic-settings.

Variable names match the "Reference: Shared Conventions & Contracts" issue (§3),
lower-cased and unprefixed. `postgres_*` fields are read but unused until M3-01.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model_base_url: str = "http://model-runner:8080/v1"
    model_name: str = "gemma-4-26b-a4b-it"

    exec_manager_url: str = "http://code-exec-manager:8090"
    exec_default_timeout_s: int = 120

    workspace_root: str = "/data/workspace"

    # Unused until M3-01 (Postgres checkpoints + metadata).
    postgres_user: str = "homeai"
    postgres_password: str = ""
    postgres_db: str = "homeai"

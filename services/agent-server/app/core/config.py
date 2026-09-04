"""Application settings, sourced from environment variables via pydantic-settings.

Variable names match `.env.example`, lower-cased and unprefixed.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model_base_url: str = "http://model-runner:8080/v1"
    model_name: str = "gemma-4-26b-a4b-it"

    exec_manager_url: str = "http://code-exec-manager:8090"
    exec_default_timeout_s: int = 120

    workspace_root: str = "/data/workspace"

    postgres_user: str = "homeai"
    postgres_password: str = ""
    postgres_db: str = "homeai"

    @property
    def postgres_dsn(self) -> str:
        """DSN for the checkpointer's Postgres connection pool.

        Hardcodes the `postgres` hostname — that's the compose service name,
        consistent with how `model_base_url`'s default already hardcodes
        `model-runner:8080` as a compose-network hostname.
        """
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@postgres:5432/{self.postgres_db}"
        )

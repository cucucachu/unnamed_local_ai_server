"""Application settings, sourced from environment variables via pydantic-settings.

Variable names match `.env.example`, lower-cased and unprefixed.
"""

from pathlib import Path

from psycopg.conninfo import make_conninfo
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres: the `platform` role and `homeai_platform` database are created
    # by the `db-init` one-shot (infra/postgres/db-init.sh), not by the
    # postgres image itself. Host/port/user/db are fixed by that script and
    # docker-compose.yml; only the password comes from `.env`.
    platform_db_host: str = "postgres"
    platform_db_port: int = 5432
    platform_db_user: str = "platform"
    platform_db_password: str = ""
    platform_db_name: str = "homeai_platform"

    # `platform-data` named volume: signing keys (`keys/`) and the bootstrap
    # `setup-code` (docs/PLATFORM.md §4).
    platform_data_dir: Path = Path("/data/platform")
    # `${SPACES_DIR}` bind mount: one `<space_id>/` tree per space
    # (`app/core/storage.py`). Must exist at startup.
    platform_spaces_dir: Path = Path("/data/spaces")

    # Pre-Stage-3 files root (`${FILES_DIR}` bind mount), moved into the
    # bootstrap admin's personal space when enabled (`app/core/legacy.py`).
    # Off until M11-02 moves the agent's file tools onto the platform.
    platform_migrate_legacy_files: bool = False
    platform_legacy_files_dir: Path = Path("/data/legacy-files")

    # Service-to-service bearer secrets for `/internal/*` (docs/PLATFORM.md §4
    # "Service-to-service auth"; `app/api/internal/service_auth.py`). Empty
    # means that service can't call anything.
    platform_agent_token: str = ""
    platform_exec_token: str = ""

    # Failed credential attempts allowed per bucket (username, client IP)
    # per window on login/setup/step-up/invite accept.
    platform_auth_rate_limit: int = 5
    platform_auth_rate_window_s: float = 60.0

    @property
    def keys_dir(self) -> Path:
        return self.platform_data_dir / "keys"

    @property
    def database_dsn(self) -> str:
        return make_conninfo(
            host=self.platform_db_host,
            port=self.platform_db_port,
            user=self.platform_db_user,
            password=self.platform_db_password,
            dbname=self.platform_db_name,
        )

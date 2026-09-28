"""Application settings, sourced from environment variables via pydantic-settings.

Variable names match `.env.example`, lower-cased and unprefixed.
"""

from ipaddress import ip_network
from pathlib import Path

from psycopg.conninfo import make_conninfo
from pydantic import field_validator
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
    # The same directory as the Docker daemon on the host sees it (`${SPACES_DIR}`):
    # the bind-mount sources `/internal/exec-grants` hands to code-exec-manager.
    # Empty refuses every exec grant.
    spaces_host_dir: str = ""

    # App builds (`app/core/appbuild.py`): the staging root (`${APP_BUILDS_DIR}`
    # bind mount; code-exec-manager mounts its per-build dirs into builder
    # containers by the matching host path), and the manager that runs them,
    # authenticated with `platform_build_token` (empty: builds are 503).
    platform_builds_dir: Path = Path("/data/builds")
    exec_manager_url: str = "http://code-exec-manager:8090"
    platform_build_token: str = ""
    # Per phase, including any wait for a free builder slot.
    platform_build_timeout_s: float = 600.0

    # Pre-Stage-3 files root (`${FILES_DIR}` bind mount), moved into the
    # bootstrap admin's personal space when enabled (`app/core/legacy.py`).
    # Compose turns it on; off here so tests opt in.
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

    # Image-shipped system apps (M14-03): Chat, Files, Settings, Home.
    # Default is `system_apps/` next to this package (`/app/system_apps` in the image).
    system_apps_dir: Path = Path(__file__).resolve().parents[2] / "system_apps"

    # WireGuard (M15-01): the sidecar's wg0.conf lives on a shared volume
    # (`wireguard-config` → `/data/wireguard`). Server keys stay under
    # `platform_data_dir/wireguard/` (0600). Endpoint is what client configs
    # put in `Endpoint =` — LAN hostname by default; set to the public
    # host:port after the human forwards UDP 51820 (docs/NETWORKING.md).
    wireguard_config_dir: Path = Path("/data/wireguard")
    wireguard_endpoint: str = "homeai.local:51820"

    # M15-02: comma-separated CIDRs for the origin classifier (`app/core/origin.py`).
    # VPN is matched first so the WireGuard tunnel is not classified as LAN
    # (10.13.13.0/24 ⊂ 10.0.0.0/8). Defaults work with no extra compose env.
    origin_vpn_subnets: str = "10.13.13.0/24"
    origin_lan_subnets: str = (
        "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,::1,fc00::/7,fe80::/10"
    )

    @field_validator("origin_vpn_subnets", "origin_lan_subnets")
    @classmethod
    def _origin_cidrs(cls, value: str) -> str:
        for part in value.split(","):
            part = part.strip()
            if part:
                ip_network(part, strict=False)
        return value

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

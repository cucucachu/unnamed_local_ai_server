"""Application settings, sourced from environment variables via pydantic-settings.

Variable names match `.env.example`, lower-cased and unprefixed - same
convention as agent-server's own `app/core/config.py`.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Delegations are verified against `{platform_url}/internal/jwks`; the
    # container's uid, gids and mounts come from `/internal/exec-grants`,
    # which only accepts `platform_exec_token` (empty: every session call
    # fails closed).
    platform_url: str = "http://platform:8100"
    platform_exec_token: str = ""

    exec_idle_minutes: int = 30
    exec_default_timeout_s: int = 120

    toolbox_image: str = "homeai-exec-toolbox:latest"

    # App builds (`app/builds.py`). `platform_build_token` authenticates the
    # platform to `/builds`. `app_builds_host_dir` is the HOST path of the
    # platform's `/data/builds` (Docker resolves bind sources on the host);
    # either empty: every build call fails closed.
    platform_build_token: str = ""
    app_builds_host_dir: str = ""
    builder_image: str = "homeai-app-builder:latest"
    build_timeout_s: int = 120
    build_concurrency: int = 2

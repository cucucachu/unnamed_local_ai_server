"""Application settings, sourced from environment variables via pydantic-settings.

Variable names match `.env.example`, lower-cased and unprefixed - same
convention as agent-server's own `app/core/config.py`.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # `populate_by_name=True`: lets tests/`create_app` callers construct
    # `Settings(files_host_dir=...)` by the Python field name directly,
    # alongside the env-var alias below (needed since env loading and
    # keyword construction otherwise only recognize one or the other).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # The HOST path from FILES_DIR (§3) - the bind-mount SOURCE for every
    # exec container this service creates. Deliberately named
    # `files_host_dir`, not `files_root` (agent-server's own name for
    # its *container-internal* mount point, `/data/files`): this
    # service's own view of the files directory (if it has one at all) is
    # irrelevant, since it's `dockerd` - not this process - that resolves the
    # bind-mount source path when creating an exec container. Passing this
    # service's own in-container path here would silently create exec
    # containers whose files bind mount points at a directory that only
    # exists inside *this* container's filesystem, not the host's.
    #
    # `validation_alias`: the env var is `FILES_DIR` (§3's one shared
    # name for the files directory across every service), not the
    # pattern-derived `FILES_HOST_DIR` pydantic-settings would otherwise
    # look for from this field's own name.
    files_host_dir: str = Field(
        default="/srv/homeai/files", validation_alias="FILES_DIR"
    )

    homeai_uid: int = 1000
    homeai_gid: int = 1000

    exec_idle_minutes: int = 30
    exec_default_timeout_s: int = 120

    toolbox_image: str = "homeai-exec-toolbox:latest"

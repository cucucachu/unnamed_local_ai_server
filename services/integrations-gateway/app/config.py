from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # The platform: grants, approval markers, audit (`/internal/*`), with our own token.
    platform_url: str = "http://platform:8100"
    platform_gateway_token: str = ""
    # What the platform presents to call `/encrypt` and `/discover`. Unset refuses everything.
    integrations_gateway_token: str = ""

    # The credential key: 32 random bytes, made on first start. Only this
    # container mounts its volume; it's in neither the database nor its backups.
    key_file: Path = Path("/keys/credentials.key")

    # Every request to an MCP server goes through the egress proxy (M18-05).
    egress_proxy_url: str = ""
    # The gateway's own credential at the proxy, which opens registered
    # integrations' addresses to it alone (M18-05).
    egress_gateway_token: str = ""
    # The CA bundle that trusts the proxy's certificates (`entrypoint.sh`).
    ca_bundle: str = ""

    connect_timeout_s: float = 15.0
    call_timeout_s: float = 60.0
    # Longest result text the model gets; longer is trimmed, head and tail kept.
    result_max_chars: int = 20_000
    # How long a `needs_input` handle waits for the user's answer.
    input_handle_ttl_s: float = 86_400.0

"""Application settings, sourced from environment variables via pydantic-settings.

Variable names match `.env.example`, lower-cased and unprefixed.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model_base_url: str = "http://model-runner:8080/v1"
    model_name: str = "gemma-4-26b-a4b-it"
    # model-runner's --ctx-size (compose passes MODEL_CTX_SIZE). deepagents
    # sizes its history summarization and old-tool-arg truncation from it;
    # without it they wait for 170k tokens and never fire on a local model.
    agent_context_tokens: int = 32768
    # Per model call, prompt processing included. An uncached prompt near
    # the context limit is the slow case: minutes on a dense model.
    model_timeout_s: int = 600

    exec_manager_url: str = "http://code-exec-manager:8090"
    exec_default_timeout_s: int = 120

    web_fetch_url: str = "http://web-fetch:8000"
    # Tool-side cap on `web_fetch`'s returned text — independent of, and on
    # top of, `web-fetch`'s own server-side `FETCH_MAX_TEXT_CHARS` cap (the
    # two caps protect different things: that one bounds how much text
    # `web-fetch` extracts/holds at all, this one bounds how much of it a
    # single tool result shoves into the model's own context window).
    web_fetch_tool_max_chars: int = 30000

    # LangGraph super-steps per turn (a model call and a tool round are one
    # each). LangGraph's default of 25 stops an app build (create, several
    # edits, build, fix, rebuild) partway. It can't be unlimited; it's the
    # only stop for a model that loops on tool calls with nobody watching.
    agent_recursion_limit: int = 200

    # A chat turn keeps running when its client disconnects (M17-01); one
    # no client has watched for this long is cancelled. Unset: never.
    agent_detached_turn_timeout_s: float | None = 3600

    # Routine scheduler (M17-04). One GPU: at most `routines_max_concurrent`
    # turns of any kind may be running when a routine run starts (chats never
    # wait on routines). A run more than `routines_missed_grace_s` late is
    # recorded as missed instead; one still going after `routine_run_timeout_s`
    # is cancelled. A run's approval left unanswered for
    # `routine_approval_ttl_s` is rejected (M17-05). Off for an agent-server
    # that shares the database but mustn't run routines (the M16 candidate).
    routines_scheduler_enabled: bool = True
    routines_poll_s: float = 30
    routines_max_concurrent: int = Field(default=1, ge=1)
    routines_missed_grace_s: float = 3600
    routine_run_timeout_s: float = 1800
    routine_approval_ttl_s: float = 86400

    # Identity JWKS, the files API the agent's file tools use, and the
    # service-auth `/internal/*` routes (bootstrap admin, delegations).
    # Without a token no delegation can be minted, so chat sockets close
    # with 1011, and pre-Stage-3 threads are never handed to the admin.
    platform_url: str = "http://platform:8100"
    platform_agent_token: str = ""

    # App templates `create_app` copies (M13-02): one folder per template,
    # `examples/apps/*` in the image (Dockerfile `COPY --from=templates`).
    app_templates_dir: str = "/app/templates"

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

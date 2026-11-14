"""What the ``mcp`` service is configured with, and nothing more (release review #16).

The MCP endpoint faces the internet and holds no database connection: every tool call goes
to the API with the caller's own credential. So it gets no database URL, no master key, no
signing key and no platform or provider secrets, only what it needs to find the API, to
prove it is the mcp service (``MCP_SERVICE_SECRET``), and to answer clients. docker-compose
gives the container exactly these variables.
"""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # Shared with the API: the only way an MCP OAuth token is accepted (16+ characters).
    # It also keys the sealed requestState, so every replica can read every other's.
    mcp_service_secret: SecretStr | None = None
    # The MCP endpoint as clients see it: the OAuth resource tokens are bound to.
    mcp_resource_url: str = "http://localhost:8002/mcp"
    mcp_allowed_origins: list[str] = Field(default_factory=list)
    mera_public_url: str = "http://localhost:3000"
    # The API as clients see it (the authorization server named in the resource metadata)
    # and, when the service reaches it another way (docker-compose: http://api:8000), that.
    mera_api_url: str = "http://localhost:8000"
    mcp_api_url: str | None = None
    log_level: str = "INFO"
    log_format: str = Field(default="json", pattern="^(json|console)$")

    @field_validator("mcp_service_secret", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("mcp_service_secret")
    @classmethod
    def _secret_length(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and len(v.get_secret_value()) < 16:
            raise ValueError("MCP_SERVICE_SECRET must be at least 16 characters")
        return v

"""Configuration settings for the rate limiter application.

Uses pydantic-settings to load settings from environment variables or .env files.
"""

from __future__ import annotations

import os
from functools import lru_cache
from ipaddress import ip_address, ip_network
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Redis Configuration
    redis_url: str = Field(
        default="redis://127.0.0.1:6379/0",
        description="Redis connection URL",
    )
    redis_connect_timeout_ms: int = Field(
        default=100,
        description="Connection timeout to Redis in milliseconds",
    )
    redis_socket_timeout_ms: int = Field(
        default=100,
        description="Socket timeout for Redis operations in milliseconds",
    )

    # Node & Identity Configuration
    instance_name: str = Field(
        default="api-local",
        description="Instance name emitted in X-Served-By header",
    )
    demo_mode: bool = Field(
        default=True,
        description="If True, allows X-User-Id header for demo identity simulation",
    )
    trusted_proxies: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "::1"],
        description="List of trusted proxy IP addresses or CIDR networks",
    )

    # Server Configuration
    host: str = Field(default="0.0.0.0", description="Host to bind server")
    port: int = Field(default=8000, description="Port to bind server")

    # Security Configuration
    admin_token: str = Field(
        default="dev-admin-token",
        description="Bearer token for admin endpoints",
    )

    # Policies File Path
    policies_path: str = Field(
        default="policies.yaml",
        description="Path to YAML file defining rate limiting policies",
    )

    def is_proxy_trusted(self, client_ip: str) -> bool:
        """Check if a direct peer IP is in the trusted proxies list."""
        if not client_ip:
            return False
        try:
            addr = ip_address(client_ip)
            for proxy in self.trusted_proxies:
                if "/" in proxy:
                    if addr in ip_network(proxy, strict=False):
                        return True
                else:
                    if addr == ip_address(proxy):
                        return True
            return False
        except ValueError:
            return False


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()


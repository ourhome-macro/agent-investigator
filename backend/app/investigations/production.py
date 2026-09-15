from __future__ import annotations

import os
from typing import Any


def validate_production_infrastructure(config: Any) -> None:
    if os.getenv("DEER_FLOW_ENV", "development").lower() != "production":
        return
    failures: list[str] = []
    if getattr(getattr(config, "database", None), "backend", None) != "postgres":
        failures.append("database.backend must be postgres")
    if getattr(getattr(config, "run_events", None), "backend", None) != "db":
        failures.append("run_events.backend must be db")
    stream_type = getattr(getattr(config, "stream_bridge", None), "type", None)
    if stream_type != "redis" and not os.getenv("DEER_FLOW_STREAM_BRIDGE_REDIS_URL"):
        failures.append("Redis StreamBridge must be configured")
    for variable in ("CI_S3_ENDPOINT", "CI_S3_BUCKET", "CI_S3_ACCESS_KEY", "CI_S3_SECRET_KEY"):
        if not os.getenv(variable):
            failures.append(f"{variable} is required")
    for variable in ("CI_EMBEDDING_BASE_URL", "CI_EMBEDDING_API_KEY", "CI_EMBEDDING_MODEL"):
        if not os.getenv(variable):
            failures.append(f"{variable} is required")
    if failures:
        raise RuntimeError("Competitive Research production infrastructure is incomplete: " + "; ".join(failures))

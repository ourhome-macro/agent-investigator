from types import SimpleNamespace

import pytest

from app.investigations.production import validate_production_infrastructure


def test_production_infrastructure_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_ENV", "production")
    for name in (
        "DEER_FLOW_STREAM_BRIDGE_REDIS_URL",
        "CI_S3_ENDPOINT",
        "CI_S3_BUCKET",
        "CI_S3_ACCESS_KEY",
        "CI_S3_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    config = SimpleNamespace(
        database=SimpleNamespace(backend="sqlite"),
        run_events=SimpleNamespace(backend="memory"),
        stream_bridge=SimpleNamespace(type="memory"),
    )
    with pytest.raises(RuntimeError, match="database.backend must be postgres"):
        validate_production_infrastructure(config)


def test_production_infrastructure_accepts_postgres_redis_db_events_and_s3(monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_ENV", "production")
    monkeypatch.setenv("CI_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("CI_S3_BUCKET", "investigations")
    monkeypatch.setenv("CI_S3_ACCESS_KEY", "access")
    monkeypatch.setenv("CI_S3_SECRET_KEY", "secret")
    config = SimpleNamespace(
        database=SimpleNamespace(backend="postgres"),
        run_events=SimpleNamespace(backend="db"),
        stream_bridge=SimpleNamespace(type="redis"),
    )
    validate_production_infrastructure(config)

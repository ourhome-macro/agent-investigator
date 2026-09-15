from pathlib import Path

import yaml


def test_deep_research_compose_keeps_data_services_internal_and_healthy() -> None:
    root = Path(__file__).resolve().parents[2]
    payload = yaml.safe_load((root / "docker" / "docker-compose.deep-research.yaml").read_text(encoding="utf-8"))
    services = payload["services"]
    assert {"postgres", "minio", "minio-init", "gateway"} <= set(services)
    assert "ports" not in services["postgres"]
    assert "ports" not in services["minio"]
    assert services["postgres"]["healthcheck"]["test"]
    assert services["minio"]["healthcheck"]["test"]
    assert services["gateway"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["gateway"]["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"
    assert "postgres-data" in payload["volumes"]
    assert "minio-data" in payload["volumes"]

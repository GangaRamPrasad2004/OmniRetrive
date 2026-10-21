from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def test_request_tracing_forwards_metrics_to_observability_service(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        main.telemetry,
        "record_http_request",
        lambda **event: recorded.append(event),
    )

    response = client.get("/health", headers={"X-Request-ID": "test-request-id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-id"
    assert response.headers["X-Correlation-ID"] == "test-request-id"
    assert float(response.headers["X-Response-Time-Ms"]) >= 0
    assert len(recorded) == 1
    assert recorded[0]["method"] == "GET"
    assert recorded[0]["endpoint"] == "/health"
    assert recorded[0]["status_code"] == 200

# Copyright (c) 2025, Project Grafana Query MCP. All rights reserved.

from app.converter.grafana import convert_to_grafana


def test_convert_cpu_avg_with_uid():
    q = convert_to_grafana(
        provider="huawei",
        service="microservice",
        names=["center-sca-w101", "center-sca-w102"],
        timerange="5m",
        metric="cpu",
        agg="avg",
        datasource_uid="DS_TEST",
    )
    data = q.model_dump()
    assert data["queries"][0]["datasource"]["uid"] == "DS_TEST"
    assert "node_cpu_seconds_total" in data["queries"][0]["expr"]
    assert "center-sca-w101|center-sca-w102" in data["queries"][0]["expr"]

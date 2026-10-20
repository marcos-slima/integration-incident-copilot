"""Testes do load test (DA-88 / IIC-AUD-009).

O script existia em `app/scripts/load_test.py`, mas a documentacao e o gate
citavam `scripts/load_test.py` e `tests/test_load_testing.py`, que nunca
existiram. Estes testes cobrem o caminho de request e o calculo de metricas
sem rede: o objetivo e' provar que o script importa, mede e devolve os campos
que a documentacao promete (p50/p95/p99 e TPS).
"""

from __future__ import annotations

import httpx
import pytest

from app.scripts import load_test


class _Response:
    status_code = 200

    def json(self) -> dict[str, str]:
        return {"probable_root_cause": "root cause"}


class _SuccessClient:
    async def post(self, *args, **kwargs) -> _Response:
        return _Response()


class _TimeoutClient:
    async def post(self, *args, **kwargs):
        raise httpx.TimeoutException("timeout")


@pytest.mark.asyncio
async def test_make_request_success() -> None:
    latency, status, body = await load_test.make_request(_SuccessClient(), "http://test", {}, "")
    assert latency >= 0
    assert status == 200
    assert body == "root cause"


@pytest.mark.asyncio
async def test_make_request_timeout_returns_504() -> None:
    latency, status, body = await load_test.make_request(_TimeoutClient(), "http://test", {}, "")
    assert latency >= 0
    assert status == 504
    assert body == "timeout"


@pytest.mark.asyncio
async def test_run_load_test_reports_percentiles_and_tps(monkeypatch) -> None:
    latencias = iter([10.0, 20.0, 30.0])

    async def fake_make_request(client, url, payload, api_key):
        return next(latencias), 200, "ok"

    class _DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(load_test, "make_request", fake_make_request)
    monkeypatch.setattr(load_test.httpx, "AsyncClient", lambda: _DummyClient())

    metrics = await load_test.run_load_test("http://test", "", concurrent=2, iterations=3)

    assert metrics["summary"]["total_requests"] == 3
    assert metrics["summary"]["successful_requests"] == 3
    assert metrics["summary"]["success_rate_percent"] == 100.0
    assert metrics["latency_ms"]["p50"] == 20.0
    assert metrics["latency_ms"]["p95"] == 30.0
    assert metrics["latency_ms"]["p99"] == 30.0
    assert metrics["summary"]["requests_per_second"] > 0

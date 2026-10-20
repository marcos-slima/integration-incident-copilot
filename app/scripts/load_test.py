"""Load testing para Integration Incident Copilot (DA-88).

Este script roda testes de carga contra a API de diagnóstico,
coletando métricas como latência (p50, p95, p99) e taxa de sucesso.

Uso:
    python -m app.scripts.load_test [opcional: número de concurrentes]

Configuração via variáveis de ambiente:
    - LOAD_TEST_URL: URL da API (default: http://127.0.0.1:8000)
    - LOAD_TEST_API_KEY: API Key (default: vazio - gera se necessário)
    - LOAD_TEST_CONCURRENT: Número de requisições concurrentes (default: 100)
    - LOAD_TEST_ITERATIONS: Total de requisições por teste (default: 1000)
"""

import asyncio
import json
import os
import secrets
import statistics
import sys
import time
from datetime import UTC, datetime
from typing import Any

import httpx

# Configuração padrão
DEFAULT_URL = os.getenv("LOAD_TEST_URL", "http://127.0.0.1:8000")
DEFAULT_API_KEY = os.getenv("LOAD_TEST_API_KEY", "")
DEFAULT_CONCURRENT = int(os.getenv("LOAD_TEST_CONCURRENT", "100"))
DEFAULT_ITERATIONS = int(os.getenv("LOAD_TEST_ITERATIONS", "1000"))

# Dados de exemplo para diagnóstico
TEST_INCIDENTS = [
    {
        "description": "OData service returning 401 Unauthorized when accessing business partner endpoint",
        "interface_type": "odata",
        "logs": "",
        "payload": "",
    },
    {
        "description": "RFC connection timeout after 30 seconds when calling BAPI_MATERIAL_GETALL",
        "interface_type": "rfc",
        "logs": "",
        "payload": "",
    },
    {
        "description": "ServiceNow incident not being created after integration webhook call",
        "interface_type": "servicenow",
        "logs": "",
        "payload": "",
    },
]


async def make_request(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    api_key: str,
) -> tuple[float, int, str]:
    """Faz uma requisição e retorna (latência_ms, status_code, endpoint)."""
    start = time.time()
    try:
        response = await client.post(
            f"{url}/diagnose",
            json=payload,
            headers={"X-API-Key": api_key} if api_key else {},
            timeout=120.0,  # Timeout alto para permitir diagnósticos completos
        )
        elapsed_ms = (time.time() - start) * 1000
        return (
            elapsed_ms,
            response.status_code,
            response.json().get("probable_root_cause", "")[:100],
        )
    except httpx.TimeoutException:
        elapsed_ms = (time.time() - start) * 1000
        return elapsed_ms, 504, "timeout"
    except Exception as e:  # noqa: BLE001 - todo erro vira métrica do load test
        elapsed_ms = (time.time() - start) * 1000
        return elapsed_ms, 0, str(e)


async def run_load_test(
    url: str,
    api_key: str,
    concurrent: int,
    iterations: int,
) -> dict[str, Any]:
    """Executa o teste de carga e retorna métricas."""
    print(f"\n{'=' * 70}")
    print("LOAD TEST - Integration Incident Copilot (DA-88)")
    print(f"{'=' * 70}")
    print(f"URL: {url}")
    print(f"Concurrent requests: {concurrent}")
    print(f"Total iterations: {iterations}")
    print(f"Timestamp: {datetime.now(UTC).isoformat()}")
    print(f"{'=' * 70}\n")

    # Preparar payloads
    payloads = [TEST_INCIDENTS[i % len(TEST_INCIDENTS)] for i in range(iterations)]

    results: list[tuple[float, int, str]] = []

    # Agrupar em batches para controlar concorrência
    batch_size = concurrent
    for batch_start in range(0, iterations, batch_size):
        batch_end = min(batch_start + batch_size, iterations)
        batch = payloads[batch_start:batch_end]

        async with httpx.AsyncClient() as client:
            # Executar batch
            tasks = [make_request(client, url, payload, api_key) for payload in batch]
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)

        # Progresso
        progress = batch_end / iterations * 100
        print(f"Progresso: {progress:.1f}% ({batch_end}/{iterations})")

    # Calcular métricas
    latencies = [r[0] for r in results if r[0] > 0]
    success_count = sum(1 for r in results if r[1] == 200)
    total_count = len(results)

    if not latencies:
        print("\nNenhuma requisição bem-sucedida!")
        return {"error": "All requests failed"}

    # Percentis
    latencies_sorted = sorted(latencies)
    p50_idx = int(len(latencies_sorted) * 0.50)
    p95_idx = int(len(latencies_sorted) * 0.95)
    p99_idx = int(len(latencies_sorted) * 0.99)

    p50 = latencies_sorted[min(p50_idx, len(latencies_sorted) - 1)]
    p95 = latencies_sorted[min(p95_idx, len(latencies_sorted) - 1)]
    p99 = latencies_sorted[min(p99_idx, len(latencies_sorted) - 1)]

    # Estatísticas
    mean_latency = statistics.mean(latencies)
    median_latency = statistics.median(latencies)
    std_latency = statistics.stdev(latencies) if len(latencies) > 1 else 0
    min_latency = min(latencies)
    max_latency = max(latencies)

    success_rate = success_count / total_count * 100 if total_count > 0 else 0
    total_time = sum(latencies) / 1000  # seconds

    # TPS (throughput)
    tps = total_count / total_time if total_time > 0 else 0

    # Resultado
    metrics = {
        "summary": {
            "total_requests": total_count,
            "successful_requests": success_count,
            "failed_requests": total_count - success_count,
            "success_rate_percent": round(success_rate, 2),
            "duration_seconds": round(total_time, 2),
            "requests_per_second": round(tps, 2),
        },
        "latency_ms": {
            "min": round(min_latency, 2),
            "max": round(max_latency, 2),
            "mean": round(mean_latency, 2),
            "median": round(median_latency, 2),
            "std_dev": round(std_latency, 2),
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "p99": round(p99, 2),
        },
        "timestamps": {
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
        },
        "test_config": {
            "url": url,
            "concurrent_requests": concurrent,
            "total_iterations": iterations,
        },
    }

    # Imprimir relatório
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print("\nSummary:")
    print(f"  Total requests:       {total_count}")
    print(f"  Successful:           {success_count} ({success_rate:.2f}%)")
    print(f"  Failed:               {total_count - success_count}")
    print(f"  Duration:             {total_time:.2f}s")
    print(f"  Throughput (TPS):     {tps:.2f} requests/sec")

    print("\nLatency (ms):")
    print(f"  Min:                  {min_latency:.2f}")
    print(f"  Max:                  {max_latency:.2f}")
    print(f"  Mean:                 {mean_latency:.2f}")
    print(f"  Median:               {median_latency:.2f}")
    print(f"  Std Dev:              {std_latency:.2f}")
    print(f"  P50:                  {p50:.2f}")
    print(f"  P95:                  {p95:.2f}")
    print(f"  P99:                  {p99:.2f}")

    print("\n" + "=" * 70)
    print("END OF REPORT")
    print("=" * 70)

    return metrics


def load_test_config() -> dict[str, int]:
    """Carrega configuração do arquivo de teste."""
    config = {
        "TPS_TARGET": int(os.getenv("TPS_TARGET", "100")),
        "TOTAL_REQUESTS": int(os.getenv("TOTAL_REQUESTS", "1000")),
    }
    return config


def main():
    """Main entry point."""
    # Parse argumentos
    concurrent = DEFAULT_CONCURRENT
    iterations = DEFAULT_ITERATIONS

    if len(sys.argv) > 1:
        try:
            concurrent = int(sys.argv[1])
        except ValueError:
            print(
                f"Warning: invalid concurrent value '{sys.argv[1]}', using default {DEFAULT_CONCURRENT}"
            )

    if len(sys.argv) > 2:
        try:
            iterations = int(sys.argv[2])
        except ValueError:
            print(
                f"Warning: invalid iterations value '{sys.argv[2]}', using default {DEFAULT_ITERATIONS}"
            )

    # Run test
    api_key = DEFAULT_API_KEY
    if not api_key:
        # Gerar uma chave de teste
        api_key = secrets.token_urlsafe(32)
        print(f"Generating test API key (not saved): {api_key[:8]}...")

    metrics = asyncio.run(run_load_test(DEFAULT_URL, api_key, concurrent, iterations))

    # Salvar resultados em JSON
    output_file = f"load_test_results_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"\nResults saved to: {output_file}")

    return metrics


if __name__ == "__main__":
    main()

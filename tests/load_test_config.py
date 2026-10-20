"""Configuração para testes de load testing (DA-88).

Variáveis de configuração para os testes de carga:
    - TPS_TARGET: meta de Throughput (requests per second)
    - TOTAL_REQUESTS: número total de requisições no teste
    - CONCURRENT_REQUESTS: número de requisições simultâneas
    - LOAD_TEST_URL: URL da API a ser testada
    - LOAD_TEST_API_KEY: API Key para autenticação (opcional)
"""

import os
from typing import Final

# Meta de throughput (requests por segundo)
TPS_TARGET: Final[int] = int(os.getenv("TPS_TARGET", "100"))

# Total de requisições no teste
TOTAL_REQUESTS: Final[int] = int(os.getenv("TOTAL_REQUESTS", "1000"))

# Requisições simultâneas
CONCURRENT_REQUESTS: Final[int] = int(os.getenv("CONCURRENT_REQUESTS", "100"))

# URL da API (default: localhost para dev)
LOAD_TEST_URL: Final[str] = os.getenv("LOAD_TEST_URL", "http://127.0.0.1:8000")

# API Key (opcional - gera se não fornecida)
LOAD_TEST_API_KEY: Final[str] = os.getenv("LOAD_TEST_API_KEY", "")

# Timeout por requisição (segundos)
REQUEST_TIMEOUT: Final[float] = float(os.getenv("REQUEST_TIMEOUT", "60.0"))

# Número de iterações do load test
LOAD_TEST_ITERATIONS: Final[int] = int(os.getenv("LOAD_TEST_ITERATIONS", "1000"))

# Delay entre requisições para rate limiting (segundos)
RATE_LIMIT_DELAY: Final[float] = float(os.getenv("RATE_LIMIT_DELAY", "0.0"))

# Configurações de teste de timeout
TEST_TIMEOUT_REQUESTS: Final[int] = 20
TEST_TIMEOUT_TIMEOUT_MS: Final[float] = 5000  # 5s

# Configurações de teste de rate limiting
TEST_RATE_LIMIT_REQUESTS: Final[int] = 100
TEST_RATE_LIMIT_WINDOW_SECONDS: Final[int] = 60

FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

# DA-24: copia o uv.lock e usa --frozen - builds reproduziveis (sem
# isso, uv sync resolvia as dependencias de novo a cada build, contra
# as versoes mais recentes compativeis com pyproject.toml, nao contra
# as travadas no lockfile commitado).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app/ app/

COPY data/sample_docs/ data/sample_docs/

COPY static/ static/

# DA-24: usuario nao-root - boa pratica de seguranca para rodar em
# Kubernetes/Kyma (PodSecurityStandards de varios clusters bloqueiam
# containers rodando como root/UID 0, e o modulo API Gateway do Kyma
# roda um sidecar Istio ao lado do container - menor superficie de
# ataque no container da aplicacao importa mais nesse cenario).
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# DA-24: chama o uvicorn direto do venv (nao "uv run uvicorn ...") -
# uv run tenta ressincronizar o ambiente a cada start (incluindo
# dependencias de dev como pre-commit/virtualenv), o que derruba o
# container em redes restritas ou sem saida (bug documentado em
# docs/DEPLOY.md secao 4). Corrigido aqui na origem - docker-compose.yml
# e os manifests Kyma (deploy/kyma/) nao precisam mais sobrescrever o
# command para contornar isso.
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM node:22-slim AS frontend-build

WORKDIR /frontend

# DA-24-fix: build do frontend a partir do codigo-fonte dentro do
# Dockerfile (multi-stage) - antes disso, static/dist/ precisava ser
# gerado manualmente (npm run build + cp) antes de "docker build",
# passo documentado (e autodenomiado como pendente) em
# docs/DEPLOY.md secao 2. Um "git clone" limpo seguido de
# "docker build" falhava ou gerava imagem sem frontend, pois
# static/dist/ esta no .gitignore.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Stage Python puro — sem frontend. Usado como base do stage "final"
# (API completa) e como target do servico "reporter" no docker-compose,
# que nao precisa do frontend compilado.
FROM python:3.12-slim AS app

WORKDIR /app

RUN pip install --no-cache-dir uv

# DA-24: copia o uv.lock e usa --frozen - builds reproduziveis (sem
# isso, uv sync resolvia as dependencias de novo a cada build, contra
# as versoes mais recentes compativeis com pyproject.toml, nao contra
# as travadas no lockfile commitado).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app/ app/
COPY scripts/ scripts/

COPY data/sample_docs/ data/sample_docs/

COPY static/ static/

# DA-24: usuario nao-root - boa pratica de seguranca para rodar em
# Kubernetes/Kyma (PodSecurityStandards de varios clusters bloqueiam
# containers rodando como root/UID 0, e o modulo API Gateway do Kyma
# roda um sidecar Istio ao lado do container - menor superficie de
# ataque no container da aplicacao importa mais nesse cenario).
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Stage final — adiciona o frontend compilado ao stage "app".
# Este e o stage padrao (sem "target") usado pela API e pelo worker.
FROM app AS final

# DA-24-fix: copia o frontend buildado no estagio anterior em vez de
# exigir que static/dist/ ja exista no contexto de build.
COPY --from=frontend-build /frontend/dist static/dist

EXPOSE 8000

# Avaliacao externa (curto prazo, item 6): HEALTHCHECK explicito -
# sem ele, orquestradores (docker compose, Kyma/Kubernetes via a
# probe equivalente) so sabem que o CONTAINER esta rodando, nao que
# a API dentro dele esta respondendo (um processo travado apos o
# startup, ex. deadlock ou LLM gateway preso, continuaria "up"
# indefinidamente sem isso). Usa urllib da stdlib em vez de curl/wget
# porque a imagem base python:3.12-slim nao inclui nenhum dos dois
# (evita adicionar uma dependencia de SO so para o healthcheck).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# DA-24: chama o uvicorn direto do venv (nao "uv run uvicorn ...") -
# uv run tenta ressincronizar o ambiente a cada start (incluindo
# dependencias de dev como pre-commit/virtualenv), o que derruba o
# container em redes restritas ou sem saida (bug documentado em
# docs/DEPLOY.md secao 4). Corrigido aqui na origem - docker-compose.yml
# e os manifests Kyma (deploy/kyma/) nao precisam mais sobrescrever o
# command para contornar isso.
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

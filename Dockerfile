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

# Validacao 2026-10-07 (DEP-01): o stage Python foi dividido em
# "builder" (compiladores, uv, download de modelos) e "app" (runtime).
# Antes, gcc/cmake/make/*-dev ficavam na imagem final, e o venv puxava
# torch com CUDA (~6,3 GB de venv); agora o uv.lock resolve torch CPU
# (index pytorch-cpu em pyproject.toml) e o venv cai para ~1,5 GB.
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
        libssl-dev \
        libffi-dev \
        cmake \
        make \
        libsasl2-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Versao do uv fixada: o mesmo uv.lock com outra versao do uv pode
# resolver/instalar diferente.
RUN pip install --no-cache-dir uv==0.11.32

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# DA-24: uv.lock + --frozen = builds reproduziveis.
# DEP-01: --extra openai - o ConfigMap Kyma usa LLM_PROVIDER=openai com
# fallback azure_openai; sem o extra, langchain-openai nao existia na
# imagem e o primeiro diagnostico falhava com ImportError.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra reports --extra openai

# DEP-01: modelos baixados no build, nao no primeiro request. Antes o
# primeiro /diagnose de cada pod baixava ~120 MB do reranker e os modelos
# do fastembed do Hugging Face - cold start lento e dependencia de saida
# para huggingface.co em runtime (que muitos clusters bloqueiam).
# BM25: o fastembed 0.8.x NAO carrega o Qdrant/bm25 do cache offline (a
# descricao do modelo exige um "mock.file" que nao existe no repo - medido
# no smoke de 2026-10-07), entao o snapshot e copiado para /app/.cache/bm25
# e o app usa FASTEMBED_BM25_PATH (app/rag/retriever.py::new_sparse_model).
# O ultimo passo carrega tudo com HF_HUB_OFFLINE=1: se o cache nao bastar,
# o BUILD falha, nao o pod.
# PRELOAD_MODELS=0 pula o passo (build sem acesso ao Hugging Face); a
# aplicacao baixa sob demanda nesse caso.
ARG PRELOAD_MODELS=1
ENV HF_HOME=/app/.cache/huggingface \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed
RUN mkdir -p "$HF_HOME" "$FASTEMBED_CACHE_PATH" \
    && if [ "$PRELOAD_MODELS" = "1" ]; then \
        .venv/bin/python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')" \
        && .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
        && cp -rL "$(.venv/bin/python -c "from huggingface_hub import snapshot_download as d; print(d(repo_id='Qdrant/bm25', cache_dir='/app/.cache/hf-bm25'))")" /app/.cache/bm25 \
        && rm -rf /app/.cache/hf-bm25 \
        && HF_HUB_OFFLINE=1 .venv/bin/python -c "from sentence_transformers import CrossEncoder; from fastembed import SparseTextEmbedding, TextEmbedding; CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'); TextEmbedding('BAAI/bge-small-en-v1.5'); SparseTextEmbedding('Qdrant/bm25', specific_model_path='/app/.cache/bm25'); print('modelos carregam offline')" ; \
    else mkdir -p /app/.cache/bm25; fi

# DEP-01: o python-qpid-proton e compilado a partir do sdist. Sem
# pkg-config no builder, o build NAO encontrava o OpenSSL e gerava um
# binding sem TLS (ldd sem libssl - medido no smoke de 2026-10-07): AMQPS,
# que e o transporte do SAP Event Mesh, falhava em runtime. Agora o build
# para se o proton nao tiver SSL.
RUN .venv/bin/python -c "from proton import SSL; assert SSL.present(), 'python-qpid-proton compilado SEM SSL (falta pkg-config/libssl-dev no builder)'; print('proton com SSL')"

# Stage Python de runtime — sem frontend e sem compiladores. Base do
# stage "final" (API e worker) e target do servico "reporter" no
# docker-compose, que nao precisa do frontend compilado.
FROM python:3.12-slim AS app

WORKDIR /app

# Runtime do python-qpid-proton (AMQP/Event Mesh) precisa do libsasl2.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsasl2-2 \
    && rm -rf /var/lib/apt/lists/*

# Com os modelos embutidos (PRELOAD_MODELS=1) o runtime fica offline para o
# Hugging Face: nenhuma chamada de rede no startup do pod. PRELOAD_MODELS=0
# -> HF_HUB_OFFLINE=0 e o download sob demanda continua possivel.
ARG PRELOAD_MODELS=1
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=${PRELOAD_MODELS} \
    FASTEMBED_BM25_PATH=/app/.cache/bm25 \
    HF_HOME=/app/.cache/huggingface \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    HF_HUB_DISABLE_TELEMETRY=1

# DA-24: usuario nao-root (PodSecurityStandards; sidecar Istio no Kyma).
RUN useradd --create-home --uid 1000 appuser

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/.cache /app/.cache

COPY --chown=appuser:appuser pyproject.toml ./
COPY --chown=appuser:appuser app/ app/
COPY --chown=appuser:appuser scripts/ scripts/
# DEP-01: migrations dentro da imagem - sem isso `alembic upgrade head`
# so rodava de um checkout local, nunca como Job/initContainer no cluster.
COPY --chown=appuser:appuser alembic/ alembic/
COPY --chown=appuser:appuser alembic.ini ./

COPY --chown=appuser:appuser data/sample_docs/ data/sample_docs/

# DA-24: static/ nao e versionado; o stage "frontend-build" gera o dist/
# e o stage "final" copia para static/dist.

USER appuser

# Stage final — adiciona o frontend compilado ao stage "app".
# Este e o stage padrao (sem "target") usado pela API e pelo worker.
FROM app AS final

# DA-24-fix: copia o frontend buildado no estagio anterior em vez de
# exigir que static/dist/ ja exista no contexto de build.
COPY --from=frontend-build --chown=appuser:appuser /static/dist static/dist

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
# DEP-01: --proxy-headers - atras do Istio/Kyma o request.client.host era o
# IP do sidecar/gateway, entao o rate limit e o contador de falhas de
# autenticacao (app/auth_guard.py) agrupavam clientes distintos.
# Quais proxies sao confiaveis vem de FORWARDED_ALLOW_IPS (default do
# uvicorn: 127.0.0.1,::1). NUNCA use "*": o uvicorn passa a devolver o
# PRIMEIRO item do X-Forwarded-For, que o cliente controla - qualquer um
# trocaria de "IP" a cada tentativa. Use as faixas internas do cluster
# (ver deploy/kyma/configmap.yaml).
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

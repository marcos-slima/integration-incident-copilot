# Pipeline de Integração Contínua Remota — GitHub Actions

> **Objetivo:** Validar o código em ambiente controlado e reproduzível antes de mesclar ao branch principal.

---

## O que é

Um **Pipeline de Integração Contínua Remota** (CI) é um sistema automatizado que executa validações em um ambiente de build controlado (GitHub Actions) sempre que código é enviado para o repositório. Este é o "segundo checkpoint" após o pipeline local, garantindo que nenhum código com problemas alcance o branch principal.

Este projeto utiliza **GitHub Actions** como plataforma de CI, com dois workflows principais:
- `tests.yml` — lint, tests, coverage, security
- `quality.yml` — quality gates, migrations, LLM eval

**Visualização dos Workflows:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         GitHub Actions Workflows                            │\n├─────────────────────────────────────────────────────────────────────────────┤
│ Trigger: push / PR para master / main                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  tests.yml (jobs paralelos):                                                │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ test (lint + tests + coverage)                                        │   │
│  │  ├── ruff check  → Linting estático                                   │   │
│  │  ├── ruff format → Verifica formatação                                │   │
│  │  ├── mypy        → Type-check incremental                             │   │
│  │  ├── pytest      → 1943 testes (unitários)                            │   │
│  │  └── coverage    → 80% global + 80% por módulo crítico                │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ browser (Playwright UI tests)                                         │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ compose-smoke (API startup com Docker)                                │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ security (audit + gitleaks + bandit)                                  │   │
│  │  ├── pip-audit   → CVEs nas dependências                              │   │
│  │  ├── gitleaks    → Scan de segredos (tree + history)                  │   │
│  │  └── bandit      → SAST Python                                        │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  quality.yml (jobs sequenciais):                                            │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ deterministic (QA docs/DA/DA)                                         │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ migrations_and_dashboards (Postgres + Alembic + queries)              │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ llm_eval (manual: workflow_dispatch + self-hosted runner)             │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Por que é importante

1. **Validação independente da máquina local** — executa em ambiente limpo e padronizado
2. **Detecção de erros ambientes específicos** — problemas que só aparecem em Linux, com dependências binárias, etc.
3. **Testes de integração** — executa testes que exigem infra (Postgres, Grafana, Neo4j)
4. **Segurança contínua** — `gitleaks` escaneia histórico completo, `pip-audit` verifica CVEs
5. **Garantia de qualidade** — qualidade gate, migrations, 45 queries dos dashboards validadas

---

## Para que serve

- **Validar cada commit** enviado para o repositório remoto
- **Executar todos os testes** incluindo testes de integração (não executáveis localmente)
- **Detectar dependências vulneráveis** (CVEs) com `pip-audit`
- **Validar migrações de banco** (Alembic) e queries de dashboards
- **Avaliar qualidade do LLM** (manualmente, com `workflow_dispatch`)
- **Garantir consistência** entre desenvolvimento e produção (ambiente controlado)
- **Prevenir regressões** com testes automatizados em cada mudança

---

## Como funciona

### Arquitetura do Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Workflow GitHub Actions                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  Trigger: push / PR para master / main                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  tests.yml (jobs paralelos):                                                │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ test (lint + tests + coverage)                                        │   │
│  │  ├── ruff check  → Linting estático                                   │   │
│  │  ├── ruff format → Verifica formatação                                │   │
│  │  ├── mypy        → Type-check incremental                             │   │
│  │  ├── pytest      → 1943 testes (unitários)                            │   │
│  │  └── coverage    → 80% global + 80% por módulo crítico                │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ browser (Playwright UI tests)                                         │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ compose-smoke (API startup com Docker)                                │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ security (audit + gitleaks + bandit)                                  │   │
│  │  ├── pip-audit   → CVEs nas dependências                              │   │
│  │  ├── gitleaks    → Scan de segredos (tree + history)                  │   │
│  │  └── bandit      → SAST Python                                        │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  quality.yml (jobs sequenciais):                                            │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ deterministic (QA docs/DA/DA)                                         │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ migrations_and_dashboards (Postgres + Alembic + queries)              │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ llm_eval (manual: workflow_dispatch + self-hosted runner)             │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Fluxo de execução

```
git push origin feat/branch
        ↓
  GitHub Actions (ubuntu-latest)
        ↓
  ┌────────────────────────────────────────────┐
  │ tests.yml                                 │
  ├────────────────────────────────────────────┤
  │ ✅ test: ruff, ruff-format, mypy, pytest │
  │ ✅ browser: Playwright UI tests           │
  │ ✅ compose-smoke: Docker startup          │
  │ ✅ security: pip-audit, gitleaks, bandit  │
  └────────────────────────────────────────────┘
        ↓
  ┌────────────────────────────────────────────┐
  │ quality.yml                               │
  ├────────────────────────────────────────────┤
  │ ✅ deterministic: 23 quality gates        │
  │ ✅ migrations: Alembic + 45 queries       │
  │ ✅ llm_eval: promptfoo (manual)           │
  └────────────────────────────────────────────┘
        ↓
  Se todos ✅ → Merge OK
  Se qualquer ❌ → PR bloqueada
```

### Job Details

| Workflow | Job | Propósito | Tempo estimado |
|---|---|---|---|
| tests.yml | `test` | Lint, types, tests, coverage | ~3-5 min |
| tests.yml | `browser` | UI tests com Playwright | ~3 min |
| tests.yml | `compose-smoke` | Docker startup validation | ~2 min |
| tests.yml | `security` | CVE, secrets, SAST | ~2-3 min |
| quality.yml | `deterministic` | QA docs/DA/DA | ~1-2 min |
| quality.yml | `migrations_and_dashboards` | DB + queries | ~3-4 min |
| quality.yml | `llm_eval` | LLM eval (manual) | ~5-10 min |

---

## Ciclo de vida no desenvolvimento

```
┌─────────────────────────────────────────────────────────────────────────────┐
│               Ciclo de Vida do Pipeline Remoto                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  1. SETUP (uma vez por projeto)                                           │
│     ├─ Define workflows em .github/workflows/                             │
│     ├─ Configura secrets no GitHub (variables, tokens)                    │
│     └─ Define runners (ubuntu-latest, self-hosted para LLM)               │
│                                                                           │
│  2. WORKFLOW (a cada push/PR)                                             │
│     ├─ Developer: git commit e git push                                   │
│     ├─ GitHub Actions:                                                    │
│     │  ├─ tests.yml roda:                                                 │
│     │  │  ├─ ✅ pass → next job                                           │
│     │  │  └─ ❌ fail → PR bloqueada                                       │
│     │  └─ quality.yml roda (schedule ou workflow_dispatch):               │
│     │     ├─ ✅ pass → merge OK                                           │
│     │     └─ ❌ fail → PR bloqueada                                       │
│     └─ Developer: corrige e reenvia                                     │
│                                                                           │
│  3. CRITICAL PATH (se algo falhar no CI)                                  │
│     ├─ Teste local: reexecutar os mesmos comandos do CI                   │
│     │  ├─ ruff check .  (idêntico ao CI)                                 │
│     │  ├─ ruff format --check .                                          │
│     │  ├─ gitleaks detect --source . --no-git                            │
│     │  └─ pytest -m "not integration"                                    │
│     └─ Correção local → commit → push novamente                          │
│                                                                           │
│  4. PRODUÇÃO (após merge)                                                 │
│     └─ CI remoto torna-se deploy trigger (conector com Kyma/CD)          │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Configuração do ambiente

### Arquivos de configuração

| Arquivo | Propósito |
|---|---|
| `.github/workflows/tests.yml` | Jobs: test, security, browser, compose-smoke |
| `.github/workflows/quality.yml` | Jobs: deterministic, migrations_and_dashboards, llm_eval |
| `.gitleaks.toml` | Patterns de detecção de segredos |
| `ruff.toml` | Configuração do linter |
| `.pre-commit-config.yaml` | Hooks locais (não são CI) |

### Variáveis de ambiente

| Variável | Uso | Tipo |
|---|---|---|
| `OLLAMA_HOST` | Host do Ollama (runner auto-hosted) | Variable (GitHub) |
| `QDRANT_URL` | URL do Qdrant (runner auto-hosted) | Variable (GitHub) |
| `DATABASE_URL` | PostgreSQL connection (service container) | Container env |
| `GRAFANA_DB_PASSWORD` | Senha do Grafana DB | Container env |

---

## Checklist de Validação

Antes de merge para `master` ou `main`:

- [ ] `git push origin feat/branch` → GitHub Actions roda automaticamente
- [ ] Todos os jobs `tests.yml` → `✅ pass`
- [ ] Todos os jobs `quality.yml` → `✅ pass`
- [ ] Nenhum `gitleaks` leak (tree ou history)
- [ ] Nenhum CVE (`pip-audit`)
- [ ] Nenhum `bandit` Medium/HIGH
- [ ] cobertura ≥ 80% global + por módulo crítico
- [ ] `mypy` 0 erros nos 9 módulos cobertos

---

## Erros Comuns e Soluções

| Erro | Causa | Solução |
|---|---|---|
| `uv python install: No download found` | Python 3.12.15 não está disponível no uv registry | Atualizar `.python-version` para versão disponível |
| `ruff format: N files changed` | Código não segue PEP8/black-style | `ruff format .` antes do commit |
| `gitleaks: sk-regex detected` | API key no código (ex: `API_KEY="sk-..."`) | Usar `.env` ou placeholder `API_KEY="REDACTED"` |
| `pip-audit: Could not find version` | Dependência com `+cpu` (ex: `torch==2.14.1+cpu`) | Remove `+cpu` com `sed` antes da auditoria |
| `alembic check: models != migrations` | Migration automática não reflete modelo | Revisar `alembic revision --autogenerate` |
| `Playwright not found` | Dependency ausente no environment | Adicionar `playwright` ao extras ou usar `uv run --with` |

---

**Referências:**
- `.github/workflows/tests.yml` — jobs principais
- `.github/workflows/quality.yml` — quality gates
- `scripts/quality_gate.py` — CLI do quality gate
- `.gitleaks.toml` — patterns de segredos
- `docs/CI_LOCAL.md` — pipeline CI local (pre-commit hooks)

### Trigger
- Push para `master` ou `main`
- Pull request para `master` ou `main`

### Jobs

#### Job: `test` (lint + tests + coverage)

| Passo | Comando | Propósito |
|---|---|---|
| Checkout | `actions/checkout@v4` | Clona o repo com commits completos (`fetch-depth: 1`) |
| Instalar uv | `setup-uv@v5` | Gerencia versão do Python e dependencies |
| Instalar Python | `uv python install` | Instala Python 3.12.15 (da `.python-version`) |
| Instalar deps sistema | `apt-get install` | python-qpid-proton, openssl, libsasl2 |
| Sincronizar | `uv sync --dev --extra prometheus` | Dependencies + extras prometheus |
| **ruff check** | `uv run ruff check .` | Linting estático |
| **ruff format** | `uv run ruff format --check .` | Verifica formatação (block CI se falhar) |
| **mypy** | `uv run mypy` | Type-check incremental (9 módulos críticos) |
| **pytest unit** | `uv run pytest -m "not integration"` | Testes sem infra (1943 passed) |
| **cov risco** | `uv run coverage report --fail-under=80` | Cobertura por módulo crítico (llm/agent/events) |

**Tempo estimado:** ~3-5 minutos

#### Job: `browser` (admin UI com Playwright)

| Passo | Comando | Propósito |
|---|---|---|
| Checkout | `actions/checkout@v4` | Clona repo |
| Instalar deps | `uv sync --dev` | Dependencies base |
| Instalar Playwright | `uv run playwright install` | Chromium headless |
| **pytest browser** | `uv run pytest tests/test_admin_ui_browser.py` | Testes de UI com Playwright |

**Requer:** `PLAYWRIGHT_VERSION: 1.56.0`

#### Job: `compose-smoke` (API startup com Docker)

| Passo | Comando | Propósito |
|---|---|---|
| Checkout | `actions/checkout@v4` | Clona repo |
| Criar `.env` | `: > .env` | Arquivo vazio (persistência opt-in) |
| Subir stack | `docker compose up -d api qdrant` | Start API + Qdrant |
| Healthcheck | `curl http://127.0.0.1:8000/health` | API responde sem LLM |

**Objetivo:** Validar startup da API em ambiente containerizado.

---

## 2. tests.yml — Security Audit

### Trigger
Mesmo que `tests.yml`

### Job: `security`

#### Passos

| Passo | Comando | Propósito |
|---|---|---|
| Checkout | `actions/checkout@v4` com `fetch-depth: 0` | Clona **todo o histórico** |
| Instalar uv | `setup-uv@v5` | Gerencia ambiente |
| Instalar deps sistema | `apt-get install` | python-qpid-proton, etc. |
| Sincronizar | `uv sync --dev` | Dependencies |
| **pip-audit** | `uv run pip-audit --disable-pip` | CVEs em dependências (bloqueia se falhar) |
| **gitleaks** (tree) | `gitleaks detect --no-git --source .` | Scan de segredos na tree atual |
| **gitleaks** (history) | `gitleaks detect --source .` | Scan de segredos no histórico completo (IIC-AUD-204) |
| **bandit** | `uv run bandit -r app/` | SAST Python (B608, B704, B310, etc.) |
| Upload artifacts | `upload-artifact@v4` | Relatórios security |

**Tempo estimado:** ~2-3 minutos

---

## 3. quality.yml — Quality Gates

### Trigger
- Push para `master` ou `main`
- Pull request para `master` ou `main`
- Schedule: `17 3 * * *` (03:17 UTC diário)
- `workflow_dispatch` (manual)

### Job: `deterministic` (QA determinística)

| Passo | Comando | Propósito |
|---|---|---|
| Checkout | `actions/checkout@v4` | Clona repo |
| Instalar uv | `setup-uv@v5` | Gerencia ambiente |
| Instalar Python | `uv python install` | Python 3.12.15 |
| Sincronizar | `uv sync --dev` | Dependencies |
| **Quality gate** | `uv run python scripts/quality_gate.py` | Validação docs/DA/DA, RAG dataset, prompt_digest |

**Checks incluídos:**
- `connector_validation_matrix` (matriz de validação em docs/ARCHITECTURE.md)
- `connector_coverage` (9 mecanismos × 27 produtos)
- `rag_dataset_schema` (esquema de casos)
- `prompt_digest_measured` (prompt template não mudou)
- `das_index_current` (DA index correto)
- `docs_code_references` (references de código em docs)

**Tempo estimado:** ~1-2 minutos

### Job: `migrations_and_dashboards` (Postgres + Alembic)

| Passo | Comando | Propósito |
|---|---|---|
| Checkout | `actions/checkout@v4` | Clona repo |
| PostgreSQL service | `postgres:16` | DB efêmero em container |
| Instalar uv | `setup-uv@v5` | Gerencia ambiente |
| Instalar Python | `uv python install` | Python 3.12.15 |
| Sincronizar | `uv sync --dev` | Dependencies |
| **Alembic upgrade head** | `uv run alembic upgrade head` | Migrar DB limpo |
| **Alembic check** | `uv run alembic check` | Modelos == migrations |
| **Dashboards** | `uv run python scripts/validate_dashboards.py` | 45 queries Validadas |
| **DA-52 e2e** | `pytest tests/test_contracts_e2e.py` | contrato → baseline → incidente |
| **Checkpoint Postgres** | `pytest test_checkpointing_postgres.py` | DA-69/75: concorrência |

**Tempo estimado:** ~3-4 minutos

### Job: `llm_eval` (promptfoo manual)

| Condição | `if: github.event_name == 'workflow_dispatch'` |
|---|---|
| Runner | `self-hosted` com Ollama (qwen3.6:35b-a3b) + Qdrant + corpus ingerido |

**Passos:**
1. Checkout
2. Setup Node.js (npx promptfoo)
3. Setup uv
4. Infra check (curl `http://.../api/tags`, `http://.../healthz`)
5. **promptfoo eval** (`npx --yes promptfoo@0.124.1 eval -c promptfooconfig.yaml`)
6. **Comparar baseline** (`uv run python scripts/quality_gate.py --compare-promptfoo`)

**Objetivo:** Avaliar qualidade do LLM em produção (não rodar no schedule por falta de infra).

---

## Fluxo Completo de Validação

```
git push origin feat/branch
        ↓
    GitHub Actions
        ↓
  ┌───────────────┐
  │ tests.yml     │
  │ ├─ test:      │
  │ │  ✅ ruff     │
  │ │  ✅ ruff fmt │
  │ │  ✅ mypy     │
  │ │  ✅ pytest   │
  │ │  ✅ coverage │
  │ ├─ browser:   │
  │ │  ✅ playwright│
  │ └─ compose:   │
  │    ✅ startup  │
  │               │
  │ security:     │
  │ ├─ pip-audit  │
  │ ├─ gitleaks   │
  │ ├─ bandit     │
  │ └─upload reports
  └───────────────┘
        ↓
  ┌───────────────┐
  │ quality.yml   │
  │ ├─ determinist│
  │ ├─ migrations │
  │ └─ llm_eval   │ ← Manual (workflow_dispatch)
  └───────────────┘
        ↓
  ┌───────────────┐
  │ Se todos ✅   │
  │ → Merge OK    │
  │ Se qualquer ❌│
  │ → PR bloqueada│
  └───────────────┘
```

---

## Comandos Locais Equivalentes ao CI

| CI Job | Comando Local |
|---|---|
| `test:ruff` | `uv run ruff check . && uv run ruff format --check .` |
| `test:mypy` | `uv run mypy` |
| `test:pytest` | `uv run pytest -m "not integration" -v --cov=app --cov-fail-under=80` |
| `security:pip-audit` | `uv export --no-dev --all-extras --no-emit-project --format requirements-txt > reqs.txt && sed -i 's/+cpu//' reqs.txt && uv run pip-audit --disable-pip --requirement reqs.txt` |
| `security:gitleaks` | `gitleaks detect --source . --config .gitleaks.toml --exit-code 1` |
| `security:bandit` | `uv run bandit -r app/ -ll` |
| `quality:deterministic` | `uv run python scripts/quality_gate.py` |
| `quality:migrations` | `DATABASE_URL=postgresql://iic:iic@localhost:5432/iic uv run alembic upgrade head && uv run alembic check` |

---

## Variables e Secrets do CI

| Variável | Uso | Tipo |
|---|---|---|
| `OLLAMA_HOST` | Host do Ollama (ex: `http://127.0.0.1:11434`) | Environment Variable |
| `QDRANT_URL` | URL do Qdrant (ex: `http://127.0.0.1:6333`) | Environment Variable |
| `DATABASE_URL` | PostgreSQL connection | Service Container |
| `GRAFANA_DB_PASSWORD` | Senha do Grafana DB | Service Container |

---

## Arquivos de Configuração

| Arquivo | Propósito |
|---|---|
| `.github/workflows/tests.yml` | Jobs: test, security, browser, compose-smoke |
| `.github/workflows/quality.yml` | Jobs: deterministic, migrations_and_dashboards, llm_eval |
| `.pre-commit-config.yaml` | Hooks locais (só rodam se instalados) |
| `.gitleaks.toml` | Patterns de detecção de segredos |
| `ruff.toml` | Configuração do linter |

---

## Checklist de Validação

Antes de merge para `master` ou `main`:

- [ ] `git push origin feat/branch` → GitHub Actions roda automaticamente
- [ ] Todos os jobs `tests.yml` → `✅ pass`
- [ ] Todos os jobs `quality.yml` → `✅ pass`
- [ ] Nenhum `gitleaks` leak (tree ou history)
- [ ] Nenhum CVE (`pip-audit`)
- [ ] Nenhum `bandit` Medium/HIGH
- [ ] cobertura ≥ 80% global + por módulo crítico
- [ ] `mypy` 0 erros nos 9 módulos cobertos

---

## Erros Comuns e Soluções

| Erro | Causa | Solução |
|---|---|---|
| `uv python install: No download found` | Python 3.12.15 não está disponível no uv registry | Atualizar `.python-version` para versão disponível |
| `ruff format: N files changed` | Código não segue PEP8/black-style | `ruff format .` antes do commit |
| `gitleaks: sk-regex detected` | API key no código (ex: `API_KEY="sk-..."`) | Usar `.env` ou placeholder `API_KEY="REDACTED"` |
| `pip-audit: Could not find version` | Dependência com `+cpu` (ex: `torch==2.14.1+cpu`) | OCI remove `+cpu` com `sed` antes da auditoria |
| `alembic check: models != migrations` | Migration automática não reflete modelo | Revisar `alembic revision --autogenerate` |

---

**Referências:**
- `.github/workflows/tests.yml` — jobs principais
- `.github/workflows/quality.yml` — quality gates
- `scripts/quality_gate.py` — CLI do quality gate
- `.gitleaks.toml` — patterns de segredos

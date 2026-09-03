# Ferramentas para Sustentação e Evolução do Ecossistema

> Objetivo: domínio e controle total sobre todos os artefatos gerados
> pelo arquiteto/desenvolvedor — infraestrutura, código-fonte,
> monitoramento, segurança e documentação — sem dependência de
> ferramentas fechadas ou processos manuais não rastreáveis.
>
> Organizado por camada. Cada item indica se já está em uso no
> projeto ou é uma lacuna a preencher.

## 1. Infraestrutura como Código (IaC)

| Ferramenta | Status | Propósito |
|---|---|---|
| Docker Compose | ✅ em uso | Já declara toda a stack (Qdrant, Neo4j, Langfuse) como código — base correta |
| **Ansible** | ⬜ gap | Automatiza o que hoje são scripts bash soltos (`setup_docker_stack.sh` etc.) — idempotente, versionável, reaplica o ambiente inteiro numa máquina nova em um comando |
| **Terraform** | ⬜ gap (futuro) | Só relevante quando/se parte da infra for pra nuvem (ex: BTP, AWS) — hoje tudo é local, baixa prioridade |

## 2. Controle de Versão e Qualidade de Código

| Ferramenta | Status | Propósito |
|---|---|---|
| Git | ✅ em uso | Controle de versão do código |
| Ruff | ✅ já nas deps de dev | Lint + formatação Python, rápido |
| **pre-commit** | ✅ em uso | Roda Ruff/testes automaticamente antes de cada commit — impede que código quebrado entre no histórico |
| **mypy** | ⬜ gap | Checagem de tipos estática — pega erros antes da execução, importante à medida que o grafo/conectores crescem |
| **Conventional Commits** | ⬜ gap (prática, não ferramenta) | Padroniza mensagens de commit (`feat:`, `fix:`, `docs:` — você já está usando isso intuitivamente) — habilita changelog automático depois |

## 3. CI/CD

| Ferramenta | Status | Propósito |
|---|---|---|
| **GitHub Actions** | ✅ em uso | Roda a suíte `pytest` automaticamente em cada push/PR — essencial ao publicar o repo publicamente, prova que os testes realmente passam, não só "no meu computador" |
| **act** | ⬜ gap (opcional) | Testa workflows do GitHub Actions localmente antes de commitar, sem gastar minutos de CI |

## 4. Gestão de Segredos

| Ferramenta | Status | Propósito |
|---|---|---|
| `.env` em texto puro | ⚠️ atual, frágil | Funciona local, mas não escala nem é seguro pra publicar |
| **git-secrets** ou **gitleaks** | ✅ em uso | Escaneia commits para impedir que uma credencial vaze acidentalmente pro GitHub — crítico antes de tornar o repo público |
| **SOPS** (Mozilla) | ⬜ gap (se precisar versionar segredos) | Permite commitar segredos *criptografados* no git, decriptados só localmente — mais simples que Vault pra escala de projeto pessoal |
| HashiCorp Vault | ⬜ não recomendado agora | Overkill pro tamanho atual do projeto; mencionar como conhecimento arquitetural, não implementar |

## 5. Observabilidade e Monitoramento

| Ferramenta | Status | Propósito |
|---|---|---|
| Langfuse | ✅ em uso (ainda não instrumentado no grafo) | Tracing específico de LLM/agente — tokens, latência, custo, qualidade de resposta |
| **Prometheus + Grafana** | ⬜ gap | Monitoramento de infraestrutura (CPU/RAM/GPU da APU, saúde dos containers, uptime) — coisa que o Langfuse não cobre, é observabilidade de sistema, não de agente |
| **Docker healthchecks** | ✅ parcial (já usados no `docker-compose.yml`) | Expandir cobertura pra todos os serviços, não só os que já têm |
| **Uptime Kuma** | ⬜ gap (opcional, leve) | Dashboard simples de "isso está no ar?" pros endpoints locais — mais leve que Grafana se você só quer visibilidade rápida |

## 6. Logging Centralizado

| Ferramenta | Status | Propósito |
|---|---|---|
| `docker compose logs` | ✅ em uso, manual | Funciona, mas não é pesquisável nem persistente além do ciclo de vida do container |
| **Loki + Grafana** | ⬜ gap | Agrega logs de todos os containers num lugar só, pesquisável — natural de adicionar junto com o Prometheus/Grafana acima (mesmo stack) |

## 7. Dependências e Segurança de Supply Chain

| Ferramenta | Status | Propósito |
|---|---|---|
| `uv` | ✅ em uso | Já resolve/trava dependências Python de forma reprodutível |
| **Dependabot** (nativo do GitHub) | ⬜ gap | Abre PR automático quando uma dependência tem vulnerabilidade conhecida — ativa sozinho ao publicar no GitHub, custo zero |
| **pip-audit** | ⬜ gap | Escaneia vulnerabilidades conhecidas nas dependências Python localmente, sem depender do GitHub |
| **Trivy** | ⬜ gap | Escaneia vulnerabilidades nas imagens Docker usadas (Qdrant, Neo4j, Postgres, ClickHouse etc.) |

## 8. Documentação Viva

| Ferramenta | Status | Propósito |
|---|---|---|
| README + seção "Decisões de Arquitetura" | ✅ em uso | Já começamos isso hoje — bom padrão |
| **ADRs formais** (Architecture Decision Records, ex: template MADR) | ⬜ gap | Formaliza o que já fazemos informalmente no README como arquivos numerados em `docs/adr/` — prática padrão de arquitetos seniores, cada decisão relevante (troca de modelo, guardrails etc.) vira um registro imutável e datado |
| **MkDocs** ou **Docusaurus** | ⬜ gap (quando o projeto crescer) | Transforma os `.md` do `docs/` num site navegável — vale a pena quando `docs/` passar de ~10 arquivos |

## 9. Testes e Qualidade do Agente

| Ferramenta | Status | Propósito |
|---|---|---|
| pytest | ✅ em uso (16 testes) | Regressão funcional do pipeline |
| promptfoo | ✅ em uso | Comparação/regressão de modelo e prompt |
| **Ragas / DeepEval** | ⬜ gap (já discutido) | Métricas graduais de qualidade RAG (faithfulness, relevância), complementa o binário passou/falhou |
| **coverage.py** | ⬜ gap | Mede % do código coberto por teste — identifica pontos cegos (ex: `main.py`/FastAPI ainda não tem teste nenhum) |

## 10. Backup e Recuperação de Desastre

| Ferramenta | Status | Propósito |
|---|---|---|
| Volumes Docker nomeados | ✅ em uso | Persistência básica, mas sem backup externo — se o disco falhar, perde tudo |
| **restic** ou **BorgBackup** | ⬜ gap | Backup incremental, criptografado, dos volumes (Qdrant, Neo4j, Postgres) pra um destino externo (disco separado, ou até o próprio Google Drive via rclone que você já tem configurado) |

## 11. Gestão Visual de Containers (opcional)

| Ferramenta | Status | Propósito |
|---|---|---|
| **Portainer** | ⬜ gap (opcional) | UI web pra ver/gerenciar containers sem decorar comandos `docker compose` — conveniência, não necessidade |

---

## Priorização sugerida

> **Atualização:** os itens 1 (gitleaks), 2 (GitHub Actions) e 3 (pre-commit) desta lista já foram implementados e estão em produção no repositório — ver seção "Decisões de Arquitetura" do README para detalhes. (o que realmente move a agulha primeiro)

1. **gitleaks** — antes de publicar qualquer coisa no GitHub, non-negociável
2. **GitHub Actions** (rodar pytest em CI) — prova de qualidade pública
3. **pre-commit** — barato de configurar, previne regressão de qualidade
4. **ADRs formais** — já tem o conteúdo (README), só falta formalizar a estrutura
5. **Prometheus + Grafana + Loki** — quando quiser observabilidade de infraestrutura além do Langfuse
6. **restic/BorgBackup** — antes de indexar dados que você não quer perder (ex: se um dia indexar documentação real, não só exemplos)

O resto (Terraform, Vault, Portainer, Docusaurus) fica como conhecimento arquitetural disponível, não como próxima ação — projetos desse porte não precisam disso ainda, e adicionar cedo demais é a mesma armadilha de escopo que já discutimos hoje algumas vezes.

## 12. Ferramentas de Desenvolvimento (dia a dia)

Complementam a lista acima (que é sustentação/operação) com o que
realmente se usa na hora de escrever código, depurar e experimentar.
Itens já cobertos nas seções anteriores (`uv`, `ruff`, `pytest`,
`promptfoo`, Claude Code) não se repetem aqui.

### Editor / IDE

| Ferramenta | Status | Propósito |
|---|---|---|
| **VS Code** | ✅ já instalado na máquina | Editor principal — extensões recomendadas abaixo |
| Extensão **Python** + **Pylance** | ⬜ gap | Autocomplete, navegação de código, checagem de tipos inline |
| Extensão **Ruff** | ⬜ gap | Lint/format em tempo real no editor, usando o mesmo Ruff já configurado no projeto |
| Extensão **Docker** | ⬜ gap | Ver/gerenciar containers da stack (`~/ai-stack`) direto no VS Code |
| Extensão **GitLens** | ⬜ gap | Blame inline, histórico de arquivo, navegação de branches sem sair do editor |
| Extensão **Even Better TOML** | ⬜ gap | Syntax highlighting/validação pro `pyproject.toml` |
| Claude Code (dentro do Claude Desktop) | ✅ já instalado | Par de programação com acesso real ao repositório |

### Terminal e produtividade de linha de comando

| Ferramenta | Status | Propósito |
|---|---|---|
| **tmux** ou **zellij** | ⬜ gap | Manter vários painéis vivos ao mesmo tempo — `docker compose logs -f`, servidor `uvicorn`, testes — sem múltiplas janelas de terminal soltas |
| **lazygit** | ⬜ gap | TUI pra git — stage/commit/branch visualmente, mais rápido que decorar comandos pro dia a dia |
| **lazydocker** | ⬜ gap | Equivalente do lazygit pro Docker — ver logs, status e reiniciar containers da stack sem abrir o Portainer |
| **gh** (GitHub CLI) | ⬜ gap | Criar PRs, issues e releases do terminal — útil assim que o repo for público |

### Testando a API e os dados

| Ferramenta | Status | Propósito |
|---|---|---|
| **HTTPie** ou **Bruno** | ⬜ gap | Testar o `POST /diagnose` sem escrever `curl` na mão toda vez — Bruno é interessante porque salva as coleções como arquivos de texto, versionáveis no próprio repo git |
| Qdrant Dashboard | ✅ já disponível | `localhost:6333/dashboard` — inspecionar vetores/collections direto no navegador |
| Neo4j Browser | ✅ já disponível | `localhost:7474` — quando o grafo (Neo4j) passar a ser usado de fato |
| **DBeaver** | ⬜ gap | Cliente universal de banco — útil pra inspecionar o Postgres/ClickHouse por trás do Langfuse quando precisar depurar dados de trace diretamente |

### Experimentação e depuração Python

| Ferramenta | Status | Propósito |
|---|---|---|
| **IPython** | ⬜ gap | REPL Python melhor que o padrão — autocomplete, histórico, `%time` pra medir chamadas ao LLM |
| **Jupyter** | ⬜ gap | Notebooks pra experimentar prompts/chunking/embeddings de forma iterativa antes de "oficializar" no código do grafo |
| **rich** | ⬜ gap | Prints formatados/coloridos — melhora bastante a legibilidade do `--debug` que já existe no `graph.py` |
| **ipdb** | ⬜ gap | Debugger interativo (`import ipdb; ipdb.set_trace()`) quando o debugger do VS Code não for prático (ex: dentro de um container) |

### Diagramas e comunicação técnica

| Ferramenta | Status | Propósito |
|---|---|---|
| **Mermaid** | ✅ já em uso (README) | Diagramas como texto, versionáveis no markdown |
| **draw.io / diagrams.net** | ⬜ gap (opcional) | Quando o diagrama precisar de mais controle visual do que o Mermaid permite |

---

### Prioridade prática pra hoje

Se for escolher só 3 pra instalar agora: **extensão Ruff + Python no VS Code** (fecha o loop de lint/format em tempo real), **lazydocker** (visibilidade rápida da stack sem decorar comando), e **HTTPie ou Bruno** (testar o `/diagnose` sem reescrever curl toda hora). O resto entra conforme a necessidade aparecer — não vale instalar tudo de uma vez só por completude.

## 13. Criação e Configuração de Ambientes de Desenvolvimento

Como o projeto já gerencia bibliotecas/dependências hoje, e o que
falta pra isso ser totalmente reprodutível por qualquer pessoa (ou
por você mesmo, numa máquina nova).

### O que já está certo

- **Separação de ambientes por propósito**: `~/ai-lab` (Python de
  estudo/experimentação) é isolado de `~/integration-incident-copilot`
  (o projeto em si) — cada um com seu próprio `uv`/Python 3.12,
  sem contaminação cruzada de dependências
- **`uv` como gerenciador único**: resolve, trava e instala
  dependências de forma determinística — `pyproject.toml` declara o
  que é necessário, `uv.lock` (gerado automaticamente) trava as
  versões exatas resolvidas
- **Separação `dependencies` vs `[project.optional-dependencies].dev`**:
  o que roda em produção (`fastapi`, `langgraph` etc.) fica separado
  do que só serve pra desenvolver (`pytest`, `ruff`, `httpx`)

### Gap real encontrado durante o desenvolvimento

O `~/ai-lab/.env` foi criado desde o início do projeto, com
`QDRANT_URL`, `NEO4J_URI`, `LANGFUSE_HOST` etc. — **mas o código do
Copilot nunca leu esse arquivo**. `app/rag/ingest.py`,
`app/rag/retriever.py` e `app/agent/graph.py` têm a URL do Qdrant
(`http://127.0.0.1:6333`) e o modelo do LLM **hardcoded** como
constantes Python, direto no código.

Funciona hoje porque tudo roda na mesma máquina, com os mesmos
endereços fixos. Mas isso quebra no primeiro cenário realista de
sustentação: mover a stack pra outra máquina, apontar pra um Qdrant
remoto, ou rodar em produção com endpoints diferentes — exigiria
editar código-fonte em vários arquivos, em vez de mudar uma variável
de ambiente.

**Correção recomendada:** introduzir uma classe de configuração
tipada com `pydantic-settings`, lida uma vez a partir do `.env`:

```python
# app/config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    qdrant_url: str = "http://127.0.0.1:6333"
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    langfuse_host: str = "http://127.0.0.1:3000"
    ollama_host: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5-coder:32b"
    embedding_model: str = "nomic-embed-text"

    model_config = {"env_file": ".env"}


settings = Settings()
```

E então `app/rag/ingest.py`, `retriever.py` e `agent/graph.py` passam
a importar `from app.config import settings` em vez de repetir
constantes soltas. Isso também resolve, de quebra, a comparação de
modelo que fizemos via `promptfoo` — trocar de modelo vira alterar
uma variável de ambiente, não um `sed` no código-fonte.

### Reprodutibilidade — "clonei o repo numa máquina nova, e agora?"

Checklist do que precisa existir pra isso funcionar sem repetir toda
a investigação manual de hoje:

1. `uv sync` — instala exatamente as mesmas versões travadas no
   `uv.lock` (**por isso o `uv.lock` deve ser versionado no git,
   nunca ignorado**)
2. `ollama pull qwen2.5-coder:32b && ollama pull nomic-embed-text` —
   os modelos não vão junto com o `git clone`, precisam ser
   documentados como pré-requisito (candidato a entrar no `README`
   ou num script `bootstrap.sh`)
3. `docker compose up -d` em `~/ai-stack` — stack de infraestrutura
4. Copiar `.env.example` (**ainda não existe** — vale criar um,
   com os nomes das variáveis mas sem valores reais) para `.env` e
   preencher

### Estratégia de atualização de dependências

- Hoje: manual, via `uv add`/`uv sync` quando necessário
- Com **Dependabot** (já mencionado na seção 7) configurado no
  GitHub, PRs de atualização de dependência (incluindo alertas de
  segurança) passam a ser automáticos — só revisar e mergear

## 14. Plugins por Ferramenta (IDs concretos)

As seções anteriores citaram "extensões"/"plugins" de forma genérica.
Aqui estão os identificadores exatos, prontos para instalar.

### VS Code (via `code --install-extension <id>` ou pela aba Extensions)

| Extensão | ID | Propósito |
|---|---|---|
| Python | `ms-python.python` | Suporte base a Python |
| Pylance | `ms-python.vscode-pylance` | Autocomplete/checagem de tipo inline |
| Ruff | `charliermarsh.ruff` | Lint/format em tempo real, usa o mesmo Ruff do projeto |
| Mypy Type Checker | `ms-python.mypy-type-checker` | Checagem de tipos inline (complementa o Pylance) |
| Docker | `ms-azuretools.vscode-docker` | Gerenciar containers/compose direto no editor |
| GitLens | `eamodio.gitlens` | Blame, histórico, navegação de branches |
| Even Better TOML | `tamasfe.even-better-toml` | Syntax/validação do `pyproject.toml` |
| YAML | `redhat.vscode-yaml` | Syntax/validação do `docker-compose.yml`, `promptfooconfig.yaml`, GitHub Actions |
| Markdown Mermaid | `bierner.markdown-mermaid` | Preview dos diagramas Mermaid do README direto no editor |
| REST Client | `humao.rest-client` | Testar o `/diagnose` com arquivos `.http` versionáveis no repo — alternativa in-editor ao Bruno/HTTPie |

Instalação em lote:
```bash
code --install-extension ms-python.python \
     --install-extension ms-python.vscode-pylance \
     --install-extension charliermarsh.ruff \
     --install-extension ms-python.mypy-type-checker \
     --install-extension ms-azuretools.vscode-docker \
     --install-extension eamodio.gitlens \
     --install-extension tamasfe.even-better-toml \
     --install-extension redhat.vscode-yaml \
     --install-extension bierner.markdown-mermaid \
     --install-extension humao.rest-client
```

### pre-commit (hooks — arquivo `.pre-commit-config.yaml` na raiz do repo)

| Hook | Repo | Propósito |
|---|---|---|
| ruff | `astral-sh/ruff-pre-commit` | Lint + format automático antes do commit |
| gitleaks | `gitleaks/gitleaks` | Bloqueia commit se detectar credencial/segredo |
| trailing-whitespace, end-of-file-fixer, check-yaml | `pre-commit/pre-commit-hooks` | Higiene básica de arquivo (o mesmo tipo de problema de whitespace que já nos mordeu no prompt do LangGraph) |

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.21.0
    hooks:
      - id: gitleaks
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
```

Ativar:
```bash
uv add --dev pre-commit
uv run pre-commit install
```

### pytest (plugins via `uv add --dev`)

| Plugin | Propósito |
|---|---|
| `pytest-cov` | Relatório de cobertura de teste integrado ao pytest (equivalente ao `coverage.py` standalone da seção 9, mas já plugado no `pytest -v --cov`) |
| `pytest-asyncio` | Necessário quando os endpoints do FastAPI (`app/main.py`) ganharem testes de verdade, já que FastAPI é assíncrono |
| `pytest-mock` | Mocka chamadas ao Ollama/Qdrant em testes unitários rápidos, sem depender da stack no ar (complementa o `conftest.py` que já pula testes de integração) |

```bash
uv add --dev pytest-cov pytest-asyncio pytest-mock
```

### tmux (via Tmux Plugin Manager — TPM)

| Plugin | Propósito |
|---|---|
| `tmux-resurrect` | Salva os painéis abertos (logs, uvicorn, etc.) e restaura depois de reiniciar a máquina |
| `tmux-continuum` | Auto-salva as sessões do `tmux-resurrect` periodicamente, sem ação manual |

### GitHub Actions (actions do marketplace, para o workflow de CI)

| Action | Propósito |
|---|---|
| `actions/checkout@v4` | Clona o repo no runner |
| `astral-sh/setup-uv@v3` | Instala o `uv` no runner do CI — mesma ferramenta usada localmente, sem duplicar lógica de dependência |
| `actions/cache@v4` | Cacheia o `uv.lock`/venv entre execuções, acelera o CI |

```yaml
# .github/workflows/tests.yml
name: tests
on: [push, pull_request]
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run pytest tests/test_connectors.py -v
        # so os testes unitarios rodam no CI - os de integracao
        # (marcados @pytest.mark.integration) precisam da stack
        # local (Qdrant/Ollama) que nao existe no runner do GitHub
```

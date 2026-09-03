# Guia de Estudos — SAP Integration Copilot

> Documento de síntese, para releitura e consolidação de aprendizado.
> Não é um log da construção do projeto — é o que restou depois de
> filtrar tentativa e erro. Para a investigação completa de qualquer
> item, os documentos-fonte estão linkados ao final de cada seção.

---

## 1. Resumo Executivo

O **SAP Integration Copilot** é um agente de IA que diagnostica
incidentes de integração SAP (OData, IDoc, RFC, CPI). Dado um
incidente — texto livre e/ou dados estruturados de um conector — ele
recupera o caso de troubleshooting mais parecido numa base de
conhecimento vetorial, usa um LLM local para produzir causa raiz e
próximos passos, e devolve um relatório em Markdown.

**Stack:** Python (`uv`), FastAPI, LangGraph, LangChain, Qdrant
(vetores), Ollama (LLM local `qwen2.5-coder:32b` + embeddings
`nomic-embed-text`), Langfuse (observabilidade), pytest, promptfoo,
Docker Compose, GitHub Actions.

**Por que importa como peça de portfólio:** não é só "um RAG que
funciona" — é um agente com **guardrails determinísticos** (não
confia cegamente no LLM), **decisões de modelo embasadas em
comparação formal**, **observabilidade real**, e um histórico
documentado de bugs reais encontrados e corrigidos com metodologia,
não achismo.

Repositório: `github.com/marcos-slima/integration-incident-copilot`

---

## 2. Arquitetura

```
Cliente HTTP
     │  POST /diagnose
     ▼
FastAPI (app/main.py)
     │  valida via Pydantic (app/models.py)
     ▼
run_diagnosis() (app/agent/graph.py)
     │
     ▼
┌─────────────────────────────────────┐
│  StateGraph (LangGraph)              │
│  connector → retrieve → diagnose → report │
└─────────────────────┬─────────────────┘
                       │
  ┌────────────┬───────┴────────┬─────────────┐
  ▼            ▼                ▼             ▼
connector    retrieve        diagnose       report
(app/         (app/rag/       (ChatOllama    (monta o
connectors/)  retriever.py     via            markdown
              → Qdrant)        with_structured_ final)
                                output)
```

**Princípio central:** o `connector` roda **antes** do `retrieve` —
dado estruturado de sistema (quando disponível) tem prioridade sobre
descrição textual do usuário, tanto na busca quanto no prompt do LLM.

📄 Aprofundamento com roteiro de debug: `docs/TUTORIAL_ARQUITETURA_DEBUG.md`

---

## 3. Código Final (estado atual, não histórico)

### Interface comum de conectores — permite trocar mock por real sem tocar no grafo

```python
# app/connectors/base.py
class SAPConnector(ABC):
    @abstractmethod
    def fetch(self, identifier: str) -> ConnectorResult:
        raise NotImplementedError

@dataclass
class ConnectorResult:
    source_system: str
    status: str
    error_code: str | None
    message: str
    raw: str
    is_mock: bool = True
    is_fallback: bool = False  # sinaliza identificador nao reconhecido
```

### Configuração centralizada — nunca hardcoded

```python
# app/config.py
class Settings(BaseSettings):
    ollama_host: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5-coder:32b"
    qdrant_url: str = "http://127.0.0.1:6333"
    langfuse_host: str = "http://127.0.0.1:3000"
    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
# Verificação: uv run python -m app.config
```

### Retriever com singleton e limiar de relevância

```python
# app/rag/retriever.py
@lru_cache(maxsize=1)
def _get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)

def retrieve(query, target="incidents", top_k=3, score_threshold=0.5):
    # score_threshold descarta resultados de baixa relevancia
    # NA PROPRIA consulta ao Qdrant, nao em Python depois
    ...
```

### Saída estruturada do LLM com guardrails em camadas

```python
# app/agent/graph.py
class DiagnosisModel(BaseModel):
    matched_source: str | None = Field(default=None, description="...")
    probable_root_cause: str = Field(description="...")
    confidence: float = Field(ge=0.0, le=1.0, description="...")
    next_steps: list[str] = Field(default_factory=list)

def _apply_confidence_guardrails(diagnosis: dict, state) -> dict:
    # Camada 1: clamp de range, defesa em profundidade
    diagnosis["confidence"] = max(0.0, min(1.0, float(diagnosis.get("confidence", 0.0))))
    # Camada 2: conector em fallback (identificador desconhecido) -> teto 0.4
    # Camada 3: nenhum contexto recuperado -> teto 0.3
    return diagnosis
```

📄 Código completo e atualizado: repositório, pasta `app/`

---

## 4. Setup Essencial

```bash
# Ambiente Python (uma vez)
cd ~/integration-incident-copilot
uv python pin 3.12   # IMPORTANTE: Langfuse e LangGraph nao suportam 3.14 de forma confiavel
uv sync

# Stack de infraestrutura (Qdrant + Neo4j + Langfuse)
cd ~/ai-stack
docker compose up -d

# Modelos Ollama necessários
ollama pull qwen2.5-coder:32b
ollama pull nomic-embed-text

# Indexar a base de conhecimento
cd ~/integration-incident-copilot
uv run python -m app.rag.ingest --target incidents

# Rodar a suíte de testes
uv run pytest -v

# Subir a API
uv run uvicorn app.main:app --reload

# Verificar configuração efetiva (sem ler código)
uv run python -m app.config
```

**Armadilha a evitar:** o `.venv` pode silenciosamente deslizar para
uma versão de Python diferente da pinada após `uv sync`/`uv add`
repetidos — sempre confirmar com `uv run python --version` se algo
parecer estranho.

---

## 5. Decisões Técnicas (o quê + por quê, resumido)

| Decisão | Por quê |
|---|---|
| LLM local via Ollama, não API paga | Hardware próprio (APU com ROCm) já disponível; controle total de dados |
| `qwen2.5-coder:32b` como modelo de produção | Comparação formal via `promptfoo` (pipeline real, não LLM isolado) mostrou comportamento mais confiável sob incerteza que os concorrentes testados (`qwen3:30b-a3b`, `qwen3.6:35b-a3b`) — ambos falharam de forma reproduzível no caso mais crítico |
| Contexto do LLM restrito ao documento top-1 | Passar os top-3 causava mistura de causa raiz entre documentos diferentes |
| `seed` fixo além de `temperature=0` | `temperature=0` sozinho não garante determinismo no Ollama |
| Guardrails de confiança no **código**, não só no prompt | LLM não é confiável para autoavaliar sua própria incerteza de forma consistente — provado por observação repetida |
| Duas collections Qdrant separadas (incidentes vs. biblioteca de referência) | Evita diluir a precisão do retriever de diagnóstico com material genérico de estudo |
| `with_structured_output` em vez de parsing manual de JSON | Mais robusto a variações de formatação — mas schema sozinho não basta, instrução em texto no prompt continua necessária pra lógica de preenchimento |

📄 Detalhamento completo: `README.md`, seção "Decisões de Arquitetura"

---

## 6. Lições Aprendidas (bugs reais, resumidos)

1. **Mistura de contexto:** LLM combinava causa raiz de documentos diferentes quando recebia top-3 inteiros → corrigido restringindo a top-1.
2. **Não-determinismo:** mesmo prompt, resultados diferentes com `temperature=0` → faltava `seed` explícito.
3. **Alucinação sob dado desconhecido:** LLM tentava vincular um documento mesmo quando o conector sinalizava identificador não reconhecido → guardrail determinístico (`is_fallback`) resolveu, não instrução de prompt.
4. **Regressão de modelo em benchmark:** candidato mais novo (`qwen3.6:35b-a3b`) parecia melhor na média, mas falhava de forma reproduzível (3/3) no caso crítico → decisão por confiabilidade no pior caso, não desempenho médio.
5. **`ensure_collection()` dentro do loop de batch:** verificação redundante rodando milhares de vezes desnecessariamente; `--reset` não deletava pontos antigos, causando duplicata permanente via `uuid4()` sempre novo.
6. **Cliente recriado a cada request:** `QdrantClient`/`OllamaEmbeddings` sem reuso de conexão — corrigido com singletons (`lru_cache`).
7. **Global mutável de módulo (`LLM_MODEL`):** risco de corrida em cenário concorrente → substituído por injeção via parâmetro/state.
8. **`Field(description=...)` não influencia geração via `with_structured_output` no Ollama** — só restringe tipo/formato. Confirmado objetivamente: outputs idênticos byte-a-byte antes/depois da mudança (esperado só se o prompt real não mudou). Correção real exigiu instrução em **texto** no prompt.
9. **Python drift para 3.14 sem aviso:** quebra silenciosa de compatibilidade com Langfuse (usa Pydantic v1 internamente, incompatível com 3.14) — sempre confirmar `python --version` após operações de dependência.

📄 Contexto completo de cada um: `docs/PROCESSO_DESENVOLVIMENTO.md`

---

## 7. Glossário de Ferramentas (o essencial)

| Ferramenta | Papel no projeto |
|---|---|
| **FastAPI** | Expõe `/diagnose` como API HTTP, validação automática |
| **Pydantic / pydantic-settings** | Contratos de dados e configuração tipada |
| **LangGraph** | Orquestra o fluxo como máquina de estados (nodes + arestas) |
| **LangChain (`langchain-ollama`, `langchain-text-splitters`)** | Integração padronizada com Ollama; divisão de documentos em chunks |
| **Qdrant** | Banco vetorial — motor de busca por similaridade semântica do RAG |
| **Ollama** | Runtime de inferência local — roda o LLM e o modelo de embedding sem depender de nuvem |
| **Langfuse** | Observabilidade — trace de cada execução (tempo, tokens, payload) |
| **pytest** | Testes de regressão automatizados |
| **promptfoo** | Comparação formal de modelos/prompts contra o pipeline real |
| **uv** | Gerenciador de ambiente/dependências Python, reprodutível |
| **Docker Compose** | Orquestra Qdrant + Neo4j + Langfuse localmente |
| **gitleaks / pre-commit** | Segurança (detecção de segredo) e qualidade (lint/format) automáticos antes de cada commit |
| **GitHub Actions** | CI — roda lint + testes a cada push |

📄 Lista completa com plugins e comandos de instalação: `docs/ferramentas-sustentacao-ecossistema.md`

---

## Onde ir a partir daqui

- **Entender o fluxo com debugger:** `docs/TUTORIAL_ARQUITETURA_DEBUG.md`
- **Ver o processo completo, fase por fase:** `docs/PROCESSO_DESENVOLVIMENTO.md`
- **Próximos passos em aberto:** conectores SAP reais (OData via SAP Business Accelerator Hub), artigo técnico, roteiro de certificações

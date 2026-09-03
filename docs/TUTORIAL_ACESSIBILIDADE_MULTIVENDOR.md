# Tutorial: LLM Gateway + Conector Multi-Vendor (Fase 8)

> Documentação completa, passo a passo, de tudo que foi criado nesta
> fase: motivo de negócio, arquitetura de solução, arquitetura de
> software, cada artefato de código gerado e cada configuração nova.
> Objetivo: você conseguir entender, rodar, debugar e reproduzir/
> estender isso sozinho, sem depender de mais ninguém.

---

## 1. Motivo de negócio (arquitetura de solução)

**Problema real:** o SAP AI Core exige SAP HANA Cloud como camada
obrigatória — custo fixo de €36.000 a €480.000/ano **independente do
quanto de IA for usado** — o que exclui, na prática, quem ainda está
em ECC on-premise (40-45% da base mundial de clientes SAP ECC, segundo
Gartner/IDC, permanecerá em legado além de 2027) ou não tem
orçamento/infra para BTP. Números e fontes completas em
[`TCO_SAP_AI_CORE_VS_SELF_HOSTED.md`](TCO_SAP_AI_CORE_VS_SELF_HOSTED.md).

**Decisão de solução:** o Copilot precisa continuar funcionando
100% local (Ollama, sem custo de API — como já era), mas também
precisa poder rodar sobre um provedor de LLM que um cliente já tenha
contratado (OpenAI/Azure OpenAI), e precisa provar, com código real
(não só texto de intenção), que integra tanto SAP quanto sistemas
não-SAP. Três decisões de software resolvem isso: **LLM Gateway
plugável**, **conector ServiceNow real** e **caminho RFC honesto para
ECC**.

---

## 2. Arquitetura de software (visão geral)

```
IncidentRequest (POST /diagnose)
        │
        ▼
   app/agent/graph.py  (LangGraph: connector → retrieve → diagnose → report)
        │                    │              │            │
        │                    │              │            └─ Markdown final
        │                    │              └─ app/llm/factory.py  ◄── NOVO
        │                    │                 (Ollama | OpenAI | Azure OpenAI)
        │                    └─ app/rag/retriever.py (Qdrant, sem mudança)
        └─ app/connectors/  (get_connector)
              ├─ odata_connector.py     (mock, sem mudança)
              ├─ rfc_connector.py       ◄── AMPLIADO (use_real + novo cenário)
              └─ servicenow_connector.py ◄── NOVO (HTTP real)
```

Nada na orquestração (`graph.py`) precisou saber *qual* provedor de
LLM ou *qual* conector está por trás — os dois são escondidos atrás de
uma interface comum (`BaseChatModel` do LangChain; `SAPConnector`/
`ExternalSystemConnector` do projeto). Isso é o que permite adicionar
peças novas sem reescrever o que já funciona.

---

## 3. Cada artefato de código gerado

### 3.1 `app/exceptions.py` (novo arquivo)

```python
class ConfigurationError(Exception):
    """Configuracao ausente ou invalida para operar em modo real."""
```

Uma exceção compartilhada, para todo código novo (`llm/factory.py`,
`rfc_connector.py`) falhar de forma previsível e identificável (`except
ConfigurationError`), em vez de cada módulo inventar seu próprio erro
ou deixar um `AttributeError` genérico vazar.

### 3.2 `app/llm/factory.py` + `app/llm/__init__.py` (novo pacote)

Função central: `get_chat_model(model_name=None, config=None)`.
Recebe `Settings`, olha `config.llm_provider` e devolve a instância
certa:

```python
if provider == "ollama":
    from langchain_ollama import ChatOllama
    return ChatOllama(model=..., base_url=cfg.ollama_host, temperature=0.0, seed=42)

if provider == "openai":
    if not cfg.openai_api_key:
        raise ConfigurationError("...")
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=..., api_key=cfg.openai_api_key, ...)

if provider == "azure_openai":
    # valida endpoint/api_key/deployment, senao ConfigurationError listando o que falta
    from langchain_openai import AzureChatOpenAI
    return AzureChatOpenAI(...)
```

**Decisão de design deliberada:** não foi criada uma interface própria
tipo `class LLMProvider(ABC): def generate(...)`. O motivo: o resto do
código já depende do contrato `BaseChatModel` do LangChain
(`.with_structured_output()`, callbacks) — todo provedor LangChain
(Ollama, OpenAI, Azure, Bedrock, Vertex, etc.) já implementa esse
mesmo contrato. Reaproveitar isso é menos código e menos lugar para
bug do que reimplementar o mesmo polimorfismo com outro nome.

**Onde entra no fluxo:** `app/agent/graph.py`, função `diagnose_node`,
uma linha mudou:

```python
# antes:
llm = ChatOllama(model=model_name, temperature=0.0, seed=42)
# depois:
llm = get_chat_model(model_name)
```

### 3.3 `app/connectors/servicenow_connector.py` (novo arquivo)

`ServiceNowConnector.fetch(identifier)`:

- Se `settings.servicenow_instance_url` estiver vazio → modo demo,
  devolve um cenário mockado (`INC0010001`, um alerta de monitoramento
  do ServiceNow apontando falha de RFC no SAP) ou um fallback genérico.
- Se estiver configurado → faz `GET
  {instance_url}/api/now/table/incident?sysparm_query=number=<id>`
  com Basic Auth de verdade, via `httpx`, e traduz a resposta JSON (ou
  erro HTTP, ou erro de rede) para `ConnectorResult`.

Isso é chamado de **conector real**, não mock, porque o caminho HTTP
completo existe e roda — só falta uma credencial de cliente real para
apontar pra uma instância de verdade. Testado com `httpx.MockTransport`
(ver §5) exatamente para provar que o código HTTP funciona, sem
depender de uma instância ServiceNow real.

### 3.4 `app/connectors/rfc_connector.py` (arquivo existente, ampliado)

Duas mudanças:

1. **Detecção de feature do `pyrfc`:**
   ```python
   try:
       import pyrfc
       HAS_PYRFC = True
   except ImportError:
       pyrfc = None
       HAS_PYRFC = False
   ```
   `RFCConnector(use_real=True)` sem `pyrfc` instalado levanta
   `ConfigurationError` explicando exatamente o que falta (o SAP
   NetWeaver RFC SDK, binário da SAP, fora do PyPI) — nunca cai
   silenciosamente no mock quando o modo real foi pedido explicitamente.

2. **Esqueleto de chamada real** (`_fetch_real`, não exercitado em CI,
   documentado para quando houver acesso a um sistema SAP real):
   ```python
   result = conn.call("BAPI_IDOC_STATUS", IDOCNUMBER=identifier)
   ```

3. **Novo cenário mock:** `RFC-GWY-POOL-TIMEOUT-DEMO` — pool de
   processos de diálogo do RFC Gateway esgotado, um problema real e
   comum em ECC on-premise sob carga batch concorrente (diferente de
   "connection refused": aqui o destino está no ar, só sem processo
   livre para atender).

### 3.5 `app/connectors/base.py` (arquivo existente, ajustado)

Adicionado o alias `ExternalSystemConnector = SAPConnector` e
atualizado o docstring do módulo — o contrato sempre foi genérico
(`source_system` sempre aceitou "outros"), só o nome da classe ficou
histórico de quando o projeto só falava com SAP. `ServiceNowConnector`
herda de `ExternalSystemConnector` (nome mais correto pra quem lê o
código novo); nada que já existia foi renomeado, para não quebrar
import nenhum.

### 3.6 `app/connectors/__init__.py` (arquivo existente, ajustado)

`_REGISTRY` ganhou uma entrada: `"servicenow": ServiceNowConnector`.

### 3.7 `app/models.py` (arquivo existente, ajustado)

`IncidentRequest.interface_type` passou de
`Literal["odata", "rfc"]` para `Literal["odata", "rfc", "servicenow"]`.

### 3.8 `app/agent/graph.py`, bloco `__main__` (arquivo existente, ajustado)

`--interface` do CLI de debug (`argparse`) também precisou aceitar
`"servicenow"`, senão o debug config novo (§6) falharia na validação
de argumento antes mesmo de chegar no código.

### 3.9 `data/sample_docs/` (dois arquivos novos)

`rfc_gateway_pool_timeout.md` e `servicenow_itsm_alert.md` — documentos
de conhecimento no mesmo formato dos 4 já existentes (Sintoma / Causas
comuns / Diagnóstico / Resolução típica), para que o RAG tenha o que
recuperar quando alguém reportar esses dois cenários novos.

---

## 4. Cada configuração gerada

### 4.1 `app/config.py` — novos campos em `Settings`

| Campo | Default | Usado por |
|---|---|---|
| `llm_provider` | `"ollama"` | `app/llm/factory.py` |
| `openai_api_key`, `openai_base_url` | `""` | idem, provider `openai` |
| `azure_openai_endpoint`, `azure_openai_api_key`, `azure_openai_deployment`, `azure_openai_api_version` | `""` / `"2024-10-21"` | idem, provider `azure_openai` |
| `sap_ashost`, `sap_sysnr`, `sap_client`, `sap_user`, `sap_password` | `""` / `"00"` / `"100"` | `RFCConnector(use_real=True)` |
| `servicenow_instance_url`, `servicenow_username`, `servicenow_password` | `""` | `ServiceNowConnector` |

Todos com default vazio/local — **nenhuma configuração nova é
obrigatória** para o comportamento atual (100% local/demo) continuar
funcionando exatamente como antes.

### 4.2 `.env.example` (novo arquivo)

Não existia antes. Lista todas as variáveis acima com comentário
explicando quando cada uma é necessária. Copie para `.env` e preencha
só o que for usar.

### 4.3 `pyproject.toml`

- `httpx` saiu de dependência só-de-dev e virou dependência principal
  (o `ServiceNowConnector` usa em runtime, não só os testes).
- Novo extra opcional: `[project.optional-dependencies] openai =
  ["langchain-openai>=0.3"]` — só instala se você rodar `uv sync
  --extra openai`, mantendo o caminho local-first default sem SDK de
  nuvem nenhum.

### 4.4 `docker-compose.yml` (novo arquivo, raiz do projeto)

Sobe `api` + `ollama` + `qdrant` com um `docker compose up -d` só —
self-contained, sem depender do `~/ai-stack` pessoal (que traz Neo4j
+ Langfuse completo, úteis no dia a dia mas não necessários só para
rodar/demonstrar o projeto uma vez). Langfuse continua opcional via
variáveis de ambiente vazias.

### 4.5 `.vscode/launch.json`

Duas configurações de debug novas, no mesmo padrão das existentes:
"Debug: graph.py (ServiceNow - alerta ITSM)" e "Debug: graph.py (RFC -
pool esgotado)" — ver §6 para como usar.

---

## 5. Testes automatizados gerados

`tests/test_llm_factory.py` (novo, 6 testes) e adições em
`tests/test_connectors.py` (7 testes novos) — **todos rodam sem
Qdrant/Ollama/ServiceNow reais**, incluindo o caminho HTTP de verdade
do `ServiceNowConnector` via `httpx.MockTransport`:

```python
def handler(request: httpx.Request) -> httpx.Response:
    assert request.url.params["sysparm_query"] == "number=INC0099999"
    return httpx.Response(200, json={"result": [{...}]})

client = httpx.Client(transport=httpx.MockTransport(handler))
result = ServiceNowConnector(client=client).fetch("INC0099999")
```

Isso prova que o código HTTP real funciona (monta a query certa,
interpreta a resposta certa, trata erro de rede e 404) sem exigir uma
instância ServiceNow de verdade — o mesmo princípio de teste que já
existia para o resto do projeto.

**Resultado antes/depois desta fase:**

| | Antes | Depois |
|---|---|---|
| Testes unitários (rodam sempre) | 5 | 18 |
| Testes de integração (pulam sem stack local) | 14 | 14 (inalterados) |
| `ruff check` | limpo | limpo |

---

## 6. Passo a passo — reproduzir e debugar do zero

```bash
# 1. Clonar e instalar
git clone https://github.com/marcos-slima/integration-incident-copilot.git
cd integration-incident-copilot
uv sync --extra dev            # so o caminho local-first
uv sync --extra dev --extra openai   # se tambem quiser testar o provider OpenAI

# 2. Rodar os testes que NAO precisam de stack nenhuma no ar
uv run pytest tests/test_connectors.py tests/test_llm_factory.py -v
uv run ruff check app/ tests/

# 3. Ver a configuracao efetiva (nada hardcoded, tudo vem daqui)
uv run python -m app.config

# 4. Subir a stack completa self-contained e testar ponta a ponta
docker compose up -d
docker compose exec ollama ollama pull qwen2.5-coder:32b
docker compose exec ollama ollama pull nomic-embed-text
uv run python -m app.rag.ingest --target incidents   # indexa data/sample_docs/
uv run pytest tests/ -v                               # agora os 14 de integracao tambem rodam

# 5. Debugar visualmente no VS Code (breakpoint em qualquer node de graph.py)
#    Rode uma das configuracoes novas em .vscode/launch.json:
#    "Debug: graph.py (ServiceNow - alerta ITSM)"
#    "Debug: graph.py (RFC - pool esgotado)"

# 6. Testar via linha de comando direto, sem debugger
uv run python -m app.agent.graph "Alerta ServiceNow aberto automaticamente" \
    --interface servicenow --id INC0010001 --debug
```

### Como estender (o padrão a seguir)

**Novo provedor de LLM** (ex: AWS Bedrock): adicionar um `elif
provider == "bedrock":` em `app/llm/factory.py`, seguindo o mesmo
formato de validação + `ConfigurationError` + import tardio (lazy) do
SDK opcional. Nenhum outro arquivo muda.

**Novo conector** (ex: Salesforce, Workday): criar
`app/connectors/salesforce_connector.py` herdando de
`ExternalSystemConnector`, seguindo exatamente o padrão do
`ServiceNowConnector` (modo demo quando não configurado, HTTP real via
`httpx` quando configurado, `client` injetável para teste). Registrar
em `app/connectors/__init__.py` (`_REGISTRY`) e no `Literal` de
`app/models.py`. Nenhuma mudança em `app/agent/graph.py` é necessária
— é exatamente esse o ponto da interface comum.

---

## 7. O que NÃO foi feito nesta fase (gap honesto)

- `RFCConnector(use_real=True)` não foi testado contra um sistema SAP
  real — não há acesso a um. O caminho existe e falha de forma
  previsível sem o SDK, mas a chamada `BAPI_IDOC_STATUS` em si não foi
  validada em produção.
- `ServiceNowConnector` não foi testado contra uma instância ServiceNow
  real, pelo mesmo motivo — só via `httpx.MockTransport`.
- Nenhuma UI foi criada — o Copilot continua sendo consumido via
  `POST /diagnose` (curl/HTTPie/Bruno) ou pela CLI de debug.

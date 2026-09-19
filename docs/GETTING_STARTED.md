# Getting Started — Integration Incident Copilot

> Do zero ao primeiro diagnóstico em menos de 10 minutos.

---

## Pré-requisitos

| Requisito | Versão mínima | Verificar |
|---|---|---|
| Python | 3.12+ | `python3 --version` |
| uv | qualquer | `uv --version` |
| Ollama | qualquer | `ollama --version` |
| Docker (opcional) | 24+ | `docker --version` |

---

## 1. Clone e configure o ambiente

```bash
git clone https://github.com/marcos-slima/sap-integration-copilot.git
cd sap-integration-copilot
cp .env.example .env
uv sync
```

---

## 2. Baixe os modelos

```bash
ollama pull qwen3-coder-next:latest
ollama pull nomic-embed-text
```

---

## 3. Suba a infraestrutura

```bash
cd ~/ai-stack
docker compose up -d
```

Sobe: Qdrant (localhost:6333), Neo4j (localhost:7474), Langfuse (localhost:3000).

---

## 4. Indexe a base de conhecimento

```bash
uv run python -m app.rag.ingest --target incidents --reset
```

Os documentos de troubleshooting ficam em data/sample_docs/ — essa é a raiz
da base de conhecimento local. O agente consulta apenas essa base,
não a internet nem nenhum serviço externo.

---

## 5. Inicie o servidor

```bash
uv run uvicorn app.main:app --reload
```

Acesse http://localhost:8000

---

## 6. Como expandir a base de conhecimento

O agente só sabe o que você ensinar. Cada documento .md em data/sample_docs/
é um caso de troubleshooting que o agente pode recuperar e usar no diagnóstico.

### Estrutura de um documento

```markdown
# [Título do Problema] — [Sistema]

## Sintoma
O que o usuário observa: código de erro, transação SAP, comportamento.

## Causas comuns
- Causa mais frequente
- Segunda causa mais comum

## Diagnóstico
1. Primeiro passo de investigação
2. Segundo passo

## Resolução típica
Ação concreta para resolver. Inclua transações SAP e critério de validação.
```

### Exemplo — novo documento

Crie o arquivo dentro de data/sample_docs/ (raiz da base de conhecimento):
data/sample_docs/bapi_authorization_failure.md

```markdown
# Falha de Autorização em BAPI — SAP ABAP

## Sintoma
Chamada RFC a uma BAPI retorna AUTHORIZATION_FAILURE.
O usuário técnico tem acesso RFC no SM59, mas a BAPI falha.

## Causas comuns
- Usuário RFC sem objeto de autorização específico da BAPI
- Role do usuário RFC não contém a transação equivalente
- Usuário bloqueado por tentativas inválidas de logon (SU01)

## Diagnóstico
1. Executar a BAPI via SE37 com o usuário RFC em modo debug
2. Verificar o log de autorização via SU53 após a falha
3. Checar bloqueio do usuário via SU01

## Resolução típica
Adicionar o objeto de autorização do SU53 ao role do usuário RFC
via PFCG. Após ajuste, testar novamente via SE37.
```

Depois de criar o arquivo, reindexe:

```bash
uv run python -m app.rag.ingest --target incidents --reset
```

A partir do próximo diagnóstico, o agente considera o novo caso.

### Boas práticas

- Use termos técnicos exatos: RFC_COMM_FAILURE, SM59, BD87, status 51
- Descreva o sintoma como o usuário descreveria ao abrir um chamado
- Um documento por problema — não agrupe problemas não relacionados
- Documente cada incidente resolvido — vira base de conhecimento da equipe
- Formatos suportados: .md (recomendado) e .pdf

---

## 7. Conectores reais

Por padrão todos os conectores operam em modo mock.
Para conectar a sistemas reais, configure no .env:

### OData / SAP Gateway
```
ODATA_SERVICE_URL=https://sistema.sap.com/sap/opu/odata/sap/API_SALES_ORDER_SRV
ODATA_OAUTH_TOKEN_URL=https://xsuaa.authentication.sap.hana.ondemand.com/oauth/token
ODATA_CLIENT_ID=client-id
ODATA_CLIENT_SECRET=secret
```

### RFC / SAP ABAP
```
SAP_ASHOST=sistema.sap.com
SAP_SYSNR=00
SAP_CLIENT=100
SAP_USER=RFC_USER
SAP_PASSWORD=senha
LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib
```

### ServiceNow
```
SERVICENOW_INSTANCE_URL=https://instancia.service-now.com
SERVICENOW_USERNAME=admin
SERVICENOW_PASSWORD=senha
```

---

## 8. Provedores LLM alternativos

```bash
# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o

# Azure OpenAI
LLM_PROVIDER=azure_openai
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://recurso.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o

# OpenRouter (qwen3-coder-next via API, sem GPU local)
LLM_PROVIDER=openai
OPENAI_API_KEY=chave-openrouter
OPENAI_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=qwen/qwen3-coder-next
```

---

## Resolução de problemas

**libsapnwrfc.so not found**
```bash
export LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib:$LD_LIBRARY_PATH
```

**Qdrant connection refused**
```bash
cd ~/ai-stack && docker compose up -d qdrant
```

**Modelo não encontrado**
```bash
ollama pull qwen3-coder-next:latest && ollama pull nomic-embed-text
```

**Confiança sempre baixa**
```bash
uv run python -m app.rag.ingest --target incidents --reset
```

---

*Integration Incident Copilot · github.com/marcos-slima/sap-integration-copilot*

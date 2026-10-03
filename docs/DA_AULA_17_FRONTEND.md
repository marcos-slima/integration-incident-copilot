# DA_AULA_17_FRONTEND.md — Frontend (Módulo 17)

## Objetivo

Documentar a interface web do Integration Incident Copilot:
- Stack: React + TypeScript + Vite
- Isolamento de camada API
- Gerenciamento de autenticação em runtime
- Models TypeScript como espelho dos Pydantic
- Componentes de UI (Diagnose, Status, History, Login)

---

## Arquitetura

### Stack

| Camada | Tecnologia |
|---|---|
| Framework | React (hooks) |
| Type system | TypeScript |
| Build tool | Vite |
| Styling | CSS Modules / Tailwind (não configurado ainda) |
| API client | `fetch()` + abstração em `api/` |

**Infra de desenvolvimento:**
- `app/main.py` serve o frontend estático (`static/dist/`) via `app/main.py::mount_frontend()`
- Dev: `npm run dev` (Vite proxy para `localhost:8000/diagnose`)
- Build: `npm run build` (gera `static/dist/`, incluído na imagem via Dockerfile)

### Isolamento da Camada API

`frontend/src/api/` contém todos os pontes para o backend:

| Arquivo | Função |
|---|---|
| `diagnose.ts` | `callDiagnose(payload) → Promise<DiagnosisResponse>` |
| `auth.ts` | `/auth/login` (sessão) + `/auth/verify/*` (email/phone) |
| `health.ts` | `getHealth() → Promise<HealthResponse>` |

**Princípio:** se a API mudar, só estes arquivos mudam.

### Autenticação

**Método atual (DA-54):** API key em `sessionStorage`:
- `STORAGE_KEY = 'integration_copilot_api_key'` (`api/diagnose.ts:24`)
- `getApiKey()` lê de `sessionStorage` (runtime, não bundle)
- `setApiKey()` persiste (válida por aba/sessão atual)

**Por que não `VITE_API_KEY`?**
- Variáveis Vite são embedadas no bundle → qualquer usuário extrai via DevTools (dev insecure)

**Evoluções futuras (DA-54):**
- Login via sessão (`POST /auth/login` → cookie HMAC HttpOnly)
- Vermificação out-of-band (token e-mail, código SMS via SMTP/SMS)

### Models TypeScript (Espelho Pydantic)

`frontend/src/types/models.ts` é **espelho exato** de:
- `app/models.py::InterfaceType`
- `app/models.py::IncidentRequest`
- `app/models.py::DiagnosisResponse`
- `app/models.py::EvidenceItem`

**Diferenças de semântica JSON:**
- `exclude_none=True` no Pydantic → campos ausentes no JSON (opcionais no TS)
- `llm_model`/`prompt_version`/`prompt_digest` omitidas quando rule engine responde sem LLM (DA-53, invariante 21)

### Componentes

| Componente | Responsabilidade |
|---|---|
| `App.tsx` | Layout (barra lateral + área de conteúdo) |
| `Sidebar.tsx` | Navegação (Diagnose, Status, History, Login) |
| `DiagnoseView.tsx` | Formulário (description, logs, payload, interface_type, identifier, sensi- tivity_level) |
| `StatusView.tsx` | `/health` — conectores, infra (LLM, GraphRAG, Langfuse, auth) |
| `HistoryView.tsx` | Histórico de diagnósticos últimos (data do mais recente) |
| `LoginView.tsx` | Input da API key (ou sessão futura) |
| `Badge.tsx` | Exibição de `diagnosis_confidence` e `evidence_strength` (tags coloridas) |

**Navegação:** React Router (não configurado ainda — placeholders no `App.tsx`).

---

## Fixtures

Nenhum fixture específico (dados são de execução real do backend).

---

## Padrões

### API Calls

```typescript
// Padrao de chamada
import { callDiagnose } from '../api/diagnose';

try {
  const result = await callDiagnose(payload);
  // handle success
} catch (err) {
  if (err instanceof ApiError) {
    // HTTP status e mensagem
  } else {
    // erro de rede / parse
  }
}
```

### Models

- Tipo da request e response **espelham Pydantic** (não extendem ou misturam)
- Só campos `nullable` ou `optional` no Pydantic viram `?` no TS (não `| null`)
- `EvidenceItem.trust_level` é `literal` union (`'system_observed' | 'retrieved_document' | ...`)

### Components

- Cada view é componente separado (`DiagnoseView`, `StatusView`, etc.)
- `Sidebar.tsx` não redireciona (placeholders) até React Router ser ativado

---

## Gates

| Gate | Origem | Verificação |
|---|---|---|
| `connector_reachable` | `app/evaluation/gates.py` | `InterfaceType` no `frontend/src/types/models.ts::InterfaceType` deve estar no pipeline (`app/connectors/__init__.py:_REGISTRY`) |
| `frontend_types_consistency` | `app/evaluation/gates.py` | Models TypeScript espelham Pydantic (verificado por字段 count + types) |

**Observação:** Gate `connector_reachable` não verifica `frontend/` porque não há mecanismo de integridade de frontend no pipeline (daí a DA-56 sugerir `frontend/src/types/models.ts` como fonte single of truth para interface_type).

---

## Exercícios

1. **Startar ambiente de desenvolvimento:**

```bash
cd frontend && npm install && npm run dev
# Verify: http://localhost:5173
```

2. **Buildar para produção:**

```bash
cd frontend && npm run build
# Gera: frontend/static/dist/index.html + bundles
```

3. **Incluir frontend na imagem:**

```bash
docker build . -t sap-integration-copilot:test
# Dockerfile copia static/dist/ (DA-24-fix)
```

4. **Testar chamada à API:**
   - Abrir DevTools → Application → Local Storage → verificar `integration_copilot_api_key`
   - Digitar API key no LoginView → Verify: `sessionStorage` contém a chave

---

## Invariantes

1. **API key nunca no bundle** — lida em runtime via `sessionStorage`, não `VITE_*`
2. **Models TypeScript espelham Pydantic** —字段 count + tipos devem bater exatamente
3. **Frontend serve estaticamente** via FastAPI (`app/main.py::mount_frontend()`)
4. **No React Router ainda** — placeholders no `App.tsx` até integração completa

---

## Limitações

1. **Sem React Router** — navegação não funcional (placeholders)
2. **Sem Styling framework** — CSS raw, não Tailwind (ainda não configurado)
3. **Sem persistência de histórico** — histórico não salvo no backend (só visualização da resposta atual)
4. **Sem notificações** — não há mecanismo de alerta ou toast

---

## Referências

- DA-24: Build reproduzível (Dockerfile multi-stage, `--frozen`, non-root user)
- DA-54: Login de sessão web (cookie HMAC + out-of-band verification)
- `frontend/src/**/*.tsx`: source frontend
- `app/models.py`: Pydantic models (espelho frontend)
- `app/main.py::mount_frontend()`: static serve
- `app/main.py::health()`: `/health` endpoint
- `docs/TROUBLESHOOTING.md`: troubleshooting geral (incluir frontend)

---

## DAs relevantes

| DA | O que é | Onde |
|---|---|---|
| DA-24 | Build reproduzível (Dockerfile multi-stage, non-root user) | `Dockerfile`, `app/main.py::mount_frontend()` |
| DA-54 | Login de sessão web (cookie HMAC + out-of-band) | `app/auth.py`, `app/main.py`, `frontend/src/api/auth.ts` |
| DA-56 | Conector SAP PO/PI on-premise (`POConnector`) | `app/connectors/po_connector.py` |
| DA-58 | Mapa de cobertura produto SAP × mecanismo | `data/sap_products.yaml`, `data/connector_coverage.yaml` |

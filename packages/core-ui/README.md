# `@copilot/core-ui` - biblioteca de UI do core

DA-95. Tudo o que qualquer produto sobre o core precisa para ter uma
interface, sem conhecer produto nenhum:

| Módulo | Conteúdo |
|---|---|
| `api/http.ts` | `configureApi`, `apiJson`, `ApiError`, `describeApiError` (mensagem pelo `code` estável) |
| `api/auth.ts` | sessão por cookie HttpOnly (DA-54) e ativação de conta (DA-55) |
| `api/diagnosis.ts` | `diagnose()` (POST /diagnose) e `getDomainPlugins()` (GET /domain-plugins) |
| `api/contracts.ts` | espelho de `DiagnosisResponse`, `Finding`, `EvidenceItem`, `CoreIncidentRequest`, `JsonSchema` |
| `i18n/` | `initI18n(catalogo_do_produto)`, textos comuns em `core.*`, `setLocale`, formatação `Intl` |
| `components/` | `SessionGate`, `LoginView`, `LanguageSelect`, `DiagnosisResult`, `FindingsPanel`, `SchemaForm`, `EvidenceList`, `Provenance`, `Badge` |
| `styles.css` | design system (tokens, layout de shell, formulário, resultado, achados) |

## Contrato com os shells

- **API pública:** só `src/index.ts` e `styles.css` (campo `exports` do
  `package.json`). Import profundo reprova no gate `import_boundaries`.
- **Sem produto:** nenhum texto, campo ou condição de produto aqui. Um
  plugin novo ganha formulário pelo JSON Schema que publica em
  `GET /domain-plugins` (`SchemaForm`); o shell só traduz os rótulos.
- **Textos:** o produto passa o próprio catálogo a `initI18n`; as chaves do
  core ficam em `core.*` e o produto não pode definir `core`.
- **Pacote de código-fonte:** o pacote exporta TypeScript, compilado pelo
  Vite/tsc do shell. Para publicar fora do monorepo (P1/P2), basta um build
  de biblioteca; os imports dos shells não mudam.

## Comandos (na raiz)

```bash
npm run typecheck -w @copilot/core-ui
npm run lint -w @copilot/core-ui
```

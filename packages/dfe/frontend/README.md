# DF-e Copilot - shell de UI (`@copilot/dfe-ui`)

Interface do produto DF-e Copilot (DA-95). Uma tela: descrição do incidente,
XML (NF-e, protocolo, `nfeProc` ou retorno de lote) e contexto fiscal; e o
resultado com fatos × hipóteses, evidências e proveniência do prompt.

- O formulário do contexto fiscal (ambiente, UF, linha do tempo) é gerado do
  JSON Schema que o plugin `dfe` publica em `GET /domain-plugins`. O contrato
  continua sendo o `FiscalContext` (`src/dfe_copilot/context.py`); o shell só
  traduz os rótulos (`context.fields.*`, `context.options.*`).
- Depende só de `@copilot/core-ui`. Nada do IIC (conectores, histórico,
  status) aparece aqui, e o gate `import_boundaries` impede o acoplamento.
- Somente leitura: a tela não oferece reenvio, cancelamento, inutilização nem
  contingência.

## Comandos (na raiz do repositório)

```bash
npm ci
npm run dev -w @copilot/dfe-ui     # Vite em :5174, API do produto em :8000
npm run build -w @copilot/dfe-ui   # gera packages/dfe/frontend/dist
uv run uvicorn dfe_copilot.main:app  # serve o dist acima (UI_DIST_DIR)
```

`dfe_copilot.main` define `UI_DIST_DIR` para este `dist`, então a API do
produto serve a interface do produto, e não a do IIC.

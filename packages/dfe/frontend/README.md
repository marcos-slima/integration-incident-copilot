# FISC — Fiscal Incident Support Copilot (`@copilot/dfe-ui`)

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
npm run dev -w @copilot/dfe-ui     # Vite em :5174, API do produto em :8001
npm run build -w @copilot/dfe-ui   # gera packages/dfe/frontend/dist
uv sync --locked --all-packages
uv run --package dfe-copilot python -m uvicorn dfe_copilot.main:app --reload --port 8001
```

`dfe_copilot.main` define `UI_DIST_DIR` para este `dist`, então a API do
produto serve a interface do produto, e não a do IIC.

## Primeiro acesso

Abra http://127.0.0.1:8001 para usar a interface compilada. Em desenvolvimento,
abra http://localhost:5174; o Vite encaminha a API para 127.0.0.1:8001.
Para outra porta, configure `DFE_API_URL` ao iniciar o Vite:
`DFE_API_URL=http://127.0.0.1:8010 npm run dev -w @copilot/dfe-ui`.

O login exige um usuário existente. “Ativar conta” confirma uma conta previamente
cadastrada pelo administrador; não cria uma conta. O cadastro persistido exige
banco configurado. Para o bootstrap local do operador, siga a seção de autenticação
do README da raiz (`WEB_UI_USERS`); nunca coloque senhas ou hashes na documentação.

O IIC em Docker pode continuar na porta 8000. A API é executada pelo pacote `dfe-copilot` do workspace, que instala o core
explicitamente. Não é necessário adicionar fontes do IIC ao `PYTHONPATH`.

# Registro histórico — README.md

> Snapshot anterior à organização documental de 2026-11-17. Não usar como
> instrução de instalação atual. Origem: [arquivo e commit](https://github.com/marcos-slima/integration-incident-copilot/blob/bf552a6b98b693ab8927da0b176eb4b23633308d/frontend/README.md).
> Links relativos foram fixados no snapshot de origem; comandos antigos são evidência histórica.

# Integration Incident Copilot - shell de UI (`@copilot/iic-ui`)

Shell de produto do IIC (DA-95). Contém só o que é do IIC: formulário com
conector/interface/identificador, IDs de demonstração, histórico da sessão e
a tela de status da stack. Sessão, login, idioma, cliente da API e o cartão
de resultado (com fatos × hipóteses) vêm de
[`@copilot/core-ui`](https://github.com/marcos-slima/integration-incident-copilot/blob/bf552a6b98b693ab8927da0b176eb4b23633308d/packages/core-ui/README.md).

## Comandos (na raiz do repositório, npm workspaces)

```bash
npm ci
npm run dev -w @copilot/iic-ui        # Vite em :5173, API em :8000 (proxy)
npm run build -w @copilot/iic-ui      # gera ../static/dist, servido pelo FastAPI
npm run typecheck -w @copilot/iic-ui
npm run lint -w @copilot/iic-ui
```

## Regras

- Importa do core só pelo índice: `import { ... } from '@copilot/core-ui'` e
  `@copilot/core-ui/styles.css`. Import profundo ou de outro produto reprova
  no gate `import_boundaries` (`architecture/boundaries.toml`).
- Textos do produto em `src/i18n/locales`; textos comuns ficam em `core.*`
  no catálogo do core-ui. O gate `ui_i18n` confere as duas pontas.
- Estilos só do IIC em `src/iic.css`; o design system é o do core-ui.

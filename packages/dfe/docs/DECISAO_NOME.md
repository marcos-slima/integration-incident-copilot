# Decisão de identidade — FISC

## Decisão

O nome formal do produto é **Fiscal Incident Support Copilot** e a marca
apresentada ao usuário é **FISC**.

## Compatibilidade técnica

Não renomeamos nesta etapa os identificadores técnicos já publicados:

| Camada | Identificador preservado |
|---|---|
| pacote Python | `dfe-copilot` / `dfe_copilot` |
| plugin | `dfe` |
| imagem | `dfe-copilot` |
| shell | `@copilot/dfe-ui` |
| cookie e variáveis | prefixos `DFE_` / `dfe_` |

Esses nomes são contratos de build, importação, deployment e integração. Uma
renomeação física exigiria migração versionada, aliases de compatibilidade,
documentação, CI e plano de rollback. A marca visível pode evoluir sem esse
risco.

## Regra de uso

Use **FISC** em telas, título do navegador e comunicação com usuários. Use
**Fiscal Incident Support Copilot** na documentação formal. Use os identificadores
técnicos existentes em código, manifests, imagens, endpoints e variáveis.

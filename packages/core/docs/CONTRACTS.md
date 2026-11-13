# Contratos públicos do core — versão 1.0

O grafo permitido é produto → SDK/core e shell → core-ui. O core não importa
produto, e um produto não importa outro. O gate de imports aplica as regras
em `architecture/boundaries.toml` da raiz do monorepo.

| Superfície | Versão | Negociação |
|---|---|---|
| HTTP | 1.0 | Header `X-Core-Contract-Version` no pedido e resposta; catálogo publica `contract_version` |
| Plugin | 1.0 | Atributo `contract_version`; core verifica antes de executar o plugin |
| SDK Python | 1.0 | `app.sdk.SDK_CONTRACT_VERSION` e `compatible_version()` |
| Componentes UI | 0.1.0 | Versão de `@copilot/core-ui`, imports somente pelo índice e styles.css |

HTTP, SDK e plugin usam major.minor: major igual e minor requerido menor ou
igual ao suportado. Versões inválidas ou incompatíveis são recusadas. Mudanças
aditivas e opcionais podem elevar minor; remover/renomear campo ou mudar seu
significado exige major novo, com migração explícita dos consumidores. Correções
que preservam contrato alteram a versão do pacote, não a versão do contrato.

Clientes HTTP antigos sem header e plugins anteriores à declaração explícita
são interpretados como 1.0. Essa compatibilidade é deliberada: a DA-95 já
publicava esses payloads. Nova funcionalidade deve declarar a versão requerida;
o DF-e declara 1.0. HTTP incompatível retorna 409 com código
`incompatible_contract`; plugin incompatível impede a carga. A UI envia a versão
e valida o catálogo antes de gerar o formulário.

Diagnóstico mantém `/diagnose`, seu request e response. O catálogo mantém a
chave `plugins` e acrescenta `contract_version` e `plugin_contract_version`.
Não houve alteração de regras fiscais ou prompts. Esse versionamento cobre
os payloads e protocolos existentes; não concede compatibilidade automática a
endpoints futuros nem elimina a necessidade dos testes de contrato.

## Plano de separação

O core já está em `packages/core`, com manifest e wheel próprios. Os produtos
já declaram `copilot-core` como dependência; no monorepo uv resolve por workspace.
Para outro repositório, remove-se a fonte workspace do manifest e fornece-se o
wheel correspondente. A publicação em registro, imagem e release independentes
do DF-e permanecem na P2; nenhuma publicação é executada por esses scripts.

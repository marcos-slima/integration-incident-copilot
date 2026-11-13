# P6 — verificação dos critérios de aceite

Revisão executada em 2026-11-13. Evidência de execução tem precedência sobre
os estados históricos da documentação. A extração física e o versionamento
dos contratos foram implementados e verificados. Imagem e CI próprios foram tratados na P2 (`ACEITE_P2.md`); homologação
fiscal permanece uma atividade distinta.

| Critério | Evidência atual | Estado |
|---|---|---|
| UI neutra e shells separados | core-ui compilado; shells IIC e DF-e compilam contra tarball fora do workspace | Verificado |
| Contexto publicado pelo plugin | Endpoint e protocolo cobertos por testes DA-95; formulário fiscal carregado no navegador autenticado | Verificado |
| Dependências em um sentido | Gate de imports; fixtures DF-e usam `app.sdk.pytest_plugin`, sem carregar arquivo do checkout do core | Verificado para imports e dependências exercitadas |
| Builds independentes | core-ui gera ESM, tipos e CSS; ambos os shells compilam contra esse pacote | Verificado |
| DF-e contra core empacotado | `scripts/test_product_isolation.py` instala wheel em diretório temporário e roda 214 testes fora do checkout | Verificado com dependências de terceiros do ambiente local |
| Core em `packages/core` | Pacote `copilot-core` independente, implementação em `src/app`, recursos e manifest próprios; IIC e DF-e declaram a dependência | Verificado; imports `app.*` preservados |
| Contratos versionados | HTTP, SDK e plugin 1.0; core-ui 0.1.0; header, catálogo, handshake do plugin e testes de incompatibilidade | Verificado; política em `packages/core/docs/CONTRACTS.md` |
| Imagem/CI próprios do DF-e | Alvo Docker e workflow próprios entregues na P2 | Ver `ACEITE_P2.md`; sem publicação/deploy remoto |

## Resultados executados

- Core: 1.968 testes aprovados, 21 pulados, 17 desmarcados por `not integration`.
- Gates, fixtures, SDK e versionamento: 172 testes direcionados aprovados.
- DF-e contra wheel instalado fora do checkout: 214 aprovados.
- Componentes de UI: quatro testes de evidência e compatibilidade HTTP.
- Typecheck, lint e builds de UI aprovados.
- Shells IIC e DF-e: typecheck e build fora do workspace, consumindo tarball
  de core-ui sem fontes TypeScript do pacote.
- Quality gate: 24 checagens sem falhas; avisos de baseline do modelo e
  benchmark do reranker permanecem informativos.

O workflow inclui as provas de distribuição; a alteração do workflow foi
verificada localmente, ainda não executada no GitHub nesta rodada.

## Validação no navegador com backend real

Sessão `admin` autenticada. Formulário de ambiente/UF/linha do tempo carregou.
Enviado relato **sintético** de duplicidade 204, homologação/SP, sem XML.
A API respondeu com fato `G-noxml`, hipótese do LLM, evidência do relato e
proveniência do prompt fiscal. Nenhuma ação fiscal foi executada.

Essa execução revelou um indicador contraditório: `no_context` aparecia como
“Evidência suficiente” quando `should_escalate=false`. A UI agora só mostra
sucesso para `grounded`; o roteamento a outro modelo não determina suficiência
de evidência. Correção coberta por testes, sem alterar o diagnóstico backend.

Isso não substitui validação fiscal, teste de emissão/SEFAZ ou homologação.
O caso de navegador não anexou XML; validações XML são cobertas pela suíte.

## Execução e primeiro acesso

API DF-e em 8001, coexistindo com IIC em Docker na 8000. O Vite usa
127.0.0.1:8001 por padrão e aceita `DFE_API_URL`. Execute a API na raiz pelo workspace, conforme o README. O produto agora é selecionado com `uv run --package dfe-copilot`; não
precisa de `PYTHONPATH`.

`WEB_UI_USERS` provisiona o operador local. A ativação por e-mail é para uma
conta já cadastrada, não cadastro público. Reinicie o servidor após mudar
credenciais no `.env`. Nenhuma credencial foi adicionada aos artefatos.

## Reprodução

```bash
npm run typecheck
npm run lint
npm test -w @copilot/core-ui
npm run build
node scripts/test_ui_distribution.mjs
uv run python scripts/test_product_isolation.py
uv run pytest tests/ -m "not integration" -q
uv run python scripts/quality_gate.py
```

## Extração concluída

A construção de core é executada em uma cópia temporária de `packages/core`,
sem fontes de nenhum produto. Um processo com imports IIC/DF-e bloqueados
inicializa o core instalado e confere o catálogo vazio. Em seguida a suíte
DF-e roda contra o mesmo wheel. As 213 dependências de registro mantiveram
exatamente suas versões anteriores à extração; o lock só acrescenta os
membros e vínculos do workspace.

O servidor DF-e local foi reiniciado após o reloader antigo ficar sem processo
de aplicação durante a mudança de diretório. A sessão pública respondeu 200
com header de contrato 1.0; um pedido de contrato 2.0 foi recusado. Sessões
anteriores podem exigir novo login porque SESSION_SECRET local é efêmero.

## Docker e limite de publicação

Build local aprovado: `docker build --build-arg PRELOAD_MODELS=0 -t
integration-incident-copilot:p6-local .`. O teste de carregamento em contêiner
temporário importou `iic_copilot.main`, verificou contrato SDK 1.0 e confirmou
o diretório de skills dentro da imagem. Nenhum contêiner existente foi
substituído. Esse teste não pré-carregou modelos nem validou infraestrutura
externa; o caminho `PRELOAD_MODELS=1` não foi repetido nesta rodada.

CI atualizado, mas ainda não executado remotamente. Sem publicação de pacote,
commit, merge ou deploy nesta rodada.

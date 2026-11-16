# CI por produto e candidato independente do FISC

## Escopos

Os workflows `tests.yml`, `quality.yml` e `dfe.yml` chamam `ci-scope.yml`.
O seletor usa o diff completo de push ou o merge-base do PR, incluindo os
caminhos antigos e novos de arquivos movidos. Não consulta apenas o último
commit e não depende do limite de arquivos dos filtros `paths` do GitHub.

| Alteração | Validação selecionada |
|---|---|
| Código, testes, dados ou UI do FISC | Workflow FISC, segurança global e quality gate |
| Shell backend/UI IIC ou deployment | Jobs IIC, segurança global e quality gate |
| Core Python, core-ui, testes compartilhados, scripts, workflows ou locks | Ambos os produtos e integrações compartilhadas |
| Manifest de qualquer workspace lido pelos dois targets Docker | Ambos os produtos |
| Apenas documentação | Quality gate e segurança; jobs pesados de produto dispensados |
| Arquivo sem classificação, diff indisponível ou primeiro push | Todos os escopos |
| Execução manual e agendamento | Todos os escopos do workflow acionado |

Os testes fiscais contra os wheels deixam de ser duplicados em `tests.yml`:
ficam em `dfe.yml`. O frontend IIC valida core-ui + shell IIC; FISC valida
core-ui + shell fiscal. Alterações compartilhadas também exercitam ambos os
shells contra o pacote distribuído de UI. O quality gate determinístico
permanece ativo em todas as alterações.

`IIC CI status`, `Quality CI status` e `FISC CI status` agregam os resultados:
aceitam jobs dispensados por escopo, mas falham em erro, cancelamento ou falha
do seletor. Não há filtros de caminho no nível do workflow que deixem esses
checks pendentes. PRs para a branch de implementação também são aceitos.
Configurar checks obrigatórios é uma ação administrativa separada; esta
alteração não muda rulesets nem permissões.

## Candidato fiscal sem release do IIC

Acione manualmente **FISC — validation and candidate** na referência que
contenha os novos workflows. O job valida core, quality gate, UI, wheels,
Compose e imagem fiscal, sem construir uma release do produto IIC.
A imagem é construída com `PRELOAD_MODELS=0`; modelos podem ser necessários
no runtime. O candidato não inclui homologação ou execução real em SAP/SEFAZ.

O artefato `fisc-candidate-<commit>-<tentativa>` contém:

- wheel fiscal com UI e recursos e wheel do core;
- `candidate.json`: versões exatas, contratos, commit, run e tentativa,
  hashes de locks, baseline de prompt e manifestos de schemas/corpus;
- `SHA256SUMS` cobrindo os arquivos e o manifesto;
- no disparo manual, `dfe-image.tar.gz`, preservando a imagem que passou no
  smoke, e seu ID local Docker registrado no manifesto.

Em push/PR, o artefato contém wheels e manifesto, sem arquivo da imagem.
O upload só acontece após todos os passos de validação do job. Os artefatos
expiram em 30 dias; não são um canal permanente de distribuição.

Para conferir um candidato baixado, extraia o artefato e execute
`sha256sum -c SHA256SUMS` no seu diretório. A presença dos dois wheels e a
correspondência com as versões declaradas são exigidas pelo gerador do manifesto.
O ID de imagem local não é um digest de registro OCI publicado.

Esta etapa não cria tags, GitHub Releases, publicação em registros ou deploy.
A política de entrega permanece em
[Versionamento e releases do FISC](https://github.com/marcos-slima/fiscal-incident-support-copilot/blob/main/docs/VERSIONAMENTO_RELEASES.md).

## Reprodução local

```bash
python3 -m unittest discover -s scripts/tests -v
uv run ruff check scripts/ci_scope.py scripts/fisc_candidate_manifest.py scripts/tests
uv run ruff format --check scripts/ci_scope.py scripts/fisc_candidate_manifest.py scripts/tests
uv run python scripts/quality_gate.py
```

Também executar a suíte exigida pelo projeto e a validação de workflows com
`actionlint`. Não confundir sintaxe e regressões locais com execução remota
aprovada: os jobs Docker, serviços e CI precisam de seus próprios resultados.

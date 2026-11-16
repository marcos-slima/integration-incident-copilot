# Distribuição das bibliotecas compartilhadas

Preparação de 2026-11-16. Os canais abaixo estão definidos para a primeira
entrega portátil. Preparar ou validar um artefato não o publica em registro.

## Canais e identidade

| Componente | Canal inicial | Evolução de registro |
|---|---|---|
| `copilot-core` | Wheel e sdist como assets de GitHub Release técnico `core/vX.Y.Z` no monorepo | PyPI com Trusted Publishing, após confirmar controle do nome e configurar publisher |
| `@copilot/core-ui` | Tarball npm como asset da release `core-ui/vX.Y.Z` | npm ou GitHub Packages somente com controle do scope `@copilot` confirmado |
| Operação do core | `copilot-core-operations-X.Y.Z.tar.gz` junto ao core da mesma versão | Permanece asset operacional versionado |
| Imagens dos produtos | Candidato de CI preparado na etapa anterior | GHCR é o destino previsto; publicação e credenciais ainda não configuradas por esta etapa |

Consulta pública em 2026-11-16 retornou HTTP 404 para os metadados PyPI de
`copilot-core` e npm de `@copilot/core-ui`. Isso não reserva nomes nem comprova
controle do scope npm. GitHub Packages exige scope correspondente ao usuário
ou organização de publicação: não presumir que a conta `marcos-slima` possa
publicar como `@copilot`. A distribuição por tarball preserva o identificador
sem depender dessa decisão. `private: true` permanece no core-ui para impedir
publicação npm acidental; não impede instalação do tarball.

Não renomear `copilot-core`, `@copilot/core-ui`, `dfe-copilot` ou `dfe_copilot`
para contornar disponibilidade de nome nesta etapa. Uma mudança de nome exigirá
migração explícita e não é necessária para o consumo portátil inicial.

## Artefatos preparados

O comando abaixo gera um diretório novo, sem misturar versões anteriores:

```bash
uv run python scripts/build_shared_distribution.py --out-dir dist/shared-libraries
uv run python scripts/verify_shared_distribution.py dist/shared-libraries
```

O builder copia apenas `packages/core` para um diretório temporário e executa
`uv build --no-sources`, gerando sdist e wheel fora do workspace. Recursos
Python, templates, textos, ontologia e skills precisam estar presentes no
pacote. A licença e o README acompanham a distribuição. O tarball npm contém
ESM, tipos, CSS, metadados, README e licença; React e demais peers continuam
como dependências do consumidor.

O pacote operacional é construído por allowlist a partir dos recursos do
monorepo. Inclui configuração Alembic, cadeia completa de migrations,
template, dashboards e validador de queries. Não inclui `.env`, credenciais,
dados de cliente, corpus fiscal ou fontes de produto. Seu `MANIFEST.json`
registra hashes de recursos e o commit de origem. Veja [operação](OPERATIONS.md).

`distribution.json` identifica versões, commit e hashes dos artefatos;
`SHA256SUMS` inclui também esse manifesto. No build local, um commit não
identifica mudanças ainda não commitadas: o resultado é somente candidato.
Para entrega, usar o build de CI do commit revisado e os mesmos arquivos
verificados, sem reconstrução silenciosa durante a promoção.

## Validações e seus limites

O verificador confere checksums, recursos, namespaces, identidade e versões.
Instala o wheel em diretório temporário, bloqueia imports dos produtos e
inicializa o core a partir desse wheel fora do checkout. O bundle operacional
é extraído com filtro seguro e sua cadeia de migrations deve ter um único head.

Esse smoke reutiliza as dependências de terceiros do ambiente e instala o
wheel com `--no-deps`. Não comprova resolução completa a partir de registro,
instalação em sistema limpo ou execução de migrations em PostgreSQL. Essas
validações continuam explícitas na etapa de consumo fora do monorepo e nos
jobs de banco. O teste de UI compila os dois shells contra um tarball, sem
fontes do core-ui, mas reutiliza dependências de terceiros locais.

## Consumir arquivos recebidos

Confira `sha256sum -c SHA256SUMS` no diretório dos artefatos. Em um ambiente
virtual do consumidor já selecionado:

```bash
uv pip install /caminho/para/copilot_core-X.Y.Z-py3-none-any.whl
npm install /caminho/para/copilot-core-ui-X.Y.Z.tgz
```

Os nomes `X.Y.Z` são marcadores, não uma release disponível. O segundo comando
é executado na raiz do projeto JavaScript consumidor. A biblioteca segue
importada como `@copilot/core-ui`; a dependência aponta ao arquivo recebido.
O primeiro comando resolve dependências reais e pode exigir rede e compilação
nativa de `python-qpid-proton`, diferentemente do smoke `--no-deps`.

No Linux, o índice CPU de torch é uma escolha do instalador/consumidor:
`tool.uv.sources` não se transforma em configuração obrigatória do wheel.
Preservar a configuração de índice CPU do monorepo ou definir política
correspondente no projeto externo, evitando assumir que o wheel carregue
esse direcionamento. Não usar `--no-deps` como instrução de instalação normal.

O manifest externo do FISC deve substituir a fonte `workspace = true` pelo
canal escolhido e fixar a versão validada. Isso será feito na validação de
consumo externo; este PR não remove os vínculos do monorepo.

## CI e promoção para release

`shared-distribution.yml` gera os arquivos, verifica o core instalado,
valida os shells contra o tarball e publica um artefato de CI com retenção de
30 dias. Jobs de distribuição executam para mudanças compartilhadas ou
acionamento manual. Upload de CI não é GitHub Release nem registro de pacotes.

Para a primeira publicação permanente:

1. Resolver a numeração e compatibilidade conforme a política de releases;
   versões declaradas nesta preparação não são incrementadas nem reservadas.
2. Validar o commit, suíte, quality gate, artefatos e matriz de consumidores.
3. Criar a tag revisada `core/vX.Y.Z` e/ou `core-ui/vX.Y.Z` no monorepo e a
   release técnica correspondente, incluindo checksums, notas e instruções.
4. Anexar os arquivos já validados: wheel/sdist/bundle operacional à release
   do core; tarball npm à release core-ui. Se promover subconjuntos, fornecer
   checksums próprios que se refiram aos arquivos efetivamente publicados.
5. Atualizar os consumidores por versão e URL permanente; não sobrescrever
   assets de uma versão publicada. Correção exige nova versão.

Tags ficam no repositório de implementação. Não criar tags de biblioteca no
repositório documental do FISC. O commit de implementação e o contrato de
plugin são distintos da versão do pacote e das versões de prompts.

## Preparação futura de registros

- **PyPI:** confirmar titularidade, cadastrar publisher GitHub para o nome
  exato do workflow/repositório e ambiente escolhido, e usar OIDC com
  `id-token: write` somente no job de publicação. Configuração de PyPI não
  é criada automaticamente por este build e não exige token persistente no código.
- **npm/GitHub Packages:** confirmar scope e direitos, escolher um único
  registro oficial e sua visibilidade, remover `private` apenas na mudança de
  publicação revisada, configurar autenticação e `publishConfig` corretos.
  Nunca embutir token no pacote ou em `.npmrc` versionado.
- **GHCR:** definir namespaces dos produtos, permissões e política de tags;
  publicar imagem e digest com vínculo ao commit validado, sem confundir ID
  local Docker com digest do registro.

Referências oficiais: [uv build/publish](https://docs.astral.sh/uv/guides/package/),
[Trusted Publishing PyPI](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
e [registro npm do GitHub](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-npm-registry).

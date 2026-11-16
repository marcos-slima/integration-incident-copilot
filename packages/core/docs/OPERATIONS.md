# Pacote operacional do Copilot Core

Este arquivo acompanha `copilot-core-operations-X.Y.Z.tar.gz`, separado do wheel.
O bundle preserva o layout de Alembic e dashboards sem exigir checkout do IIC.
A implementação Python vem do wheel `copilot-core` da mesma versão.

## Uso em ambiente já preparado

1. Confira `SHA256SUMS` antes de extrair os artefatos recebidos.
2. Instale o wheel do core e suas dependências com uv no ambiente do produto.
3. Extraia este bundle em um diretório operacional separado e entre nele.
4. Configure `DATABASE_URL` e as variáveis exigidas pelo deployment em ambiente
   seguro; nenhum `.env`, segredo ou dado de cliente acompanha o bundle.
5. Para inspecionar a cadeia, execute `uv run --no-project --no-sync alembic -c alembic.ini history`.
6. Com backup e destino conferidos, execute no ambiente do produto:
   `uv run --no-project --no-sync alembic -c alembic.ini upgrade head` e
   `uv run --no-project --no-sync alembic -c alembic.ini check`.

`--no-project --no-sync` pressupõe um ambiente virtual já instalado e selecionado
por `VIRTUAL_ENV`; não resolve dependências novas. Também é possível chamar
`alembic` diretamente pelo caminho absoluto desse ambiente.

O modo de inspeção não comprova aplicação das migrations em PostgreSQL.
Upgrade/check alteram ou consultam o banco configurado: não são executados
pelo build do bundle. Compartilhar core não significa compartilhar um banco
entre FISC e IIC. Cada deployment define seu banco e ciclo de migração.

## Recursos e validação

`MANIFEST.json` registra commit, versão do core e SHA-256 dos arquivos originais.
O bundle inclui a cadeia completa de migrations, configuração Alembic,
template, licença, dashboards e `scripts/validate_dashboards.py`.
A lista é explícita e exclui `.env`, volumes, backups, corpus fiscal e fontes
dos produtos. O wheel contém recursos de runtime, incluindo templates, textos,
ontologia e skills; estes não são substituídos pelo bundle operacional.

A validação das queries requer PostgreSQL e o script pode ser executado com
`uv run --no-project --no-sync python scripts/validate_dashboards.py --dsn "$DATABASE_URL"`.
Não coloque o valor da DSN em logs ou documentação pública.

Para atualização ou retorno, observe as notas da versão e a estratégia de
backup/restauração. Trocar o wheel não desfaz uma migration de dados.

# Integration Incident Copilot (IIC)

Assistência à investigação de incidentes de integração SAP e multivendor,
com regras determinísticas, evidências e hipóteses para revisão humana.

[English](README.md) · [Documentação](docs/README.md) · [Primeira execução](docs/GETTING_STARTED.md)

O IIC mantém o shell Python `iic_copilot`, a UI de integração, os testes do
consumidor, a imagem Docker e as configurações de implantação.
[Copilot Platform](https://github.com/marcos-slima/copilot-platform) mantém core Python,
core-ui e operações compartilhadas, com versões independentes.
[FISC](https://github.com/marcos-slima/fiscal-incident-support-copilot) mantém o domínio fiscal.

Versões adotadas: core 1.4.1, core-ui 0.1.1 e operações 1.0.0, fixadas por URL,
hash e locks. O manifest do IIC declara 1.4.0; esta migração não publicou uma
nova release do produto nem renumerou tags históricas.

Comece pelo [guia de execução](docs/GETTING_STARTED.md). Consulte
[arquitetura](docs/ARCHITECTURE.md), [implantação](docs/DEPLOY.md),
[qualidade](docs/QUALITY_GATES.md) e [versionamento](docs/VERSIONAMENTO.md).
Documentos do monorepo anterior estão no [histórico](docs/history/README.md).
Não existe cópia ativa do core neste produto; mudanças compartilhadas pertencem à plataforma.

[Contribuição](CONTRIBUTING.md) · [Licença MIT](LICENSE)

# Qualidade do consumidor IIC

O workflow `.github/workflows/ci.yml` roda em PRs e pushes na master. O gate
`scripts/quality_gate.py` confere ausência de fontes vendorizadas do core e
imports do shell pela superfície pública. Roda junto com os testes do produto.

A CI instala as dependências do lock, compila a UI, constrói/instala o wheel,
executa quatro testes de consumidor, valida Compose, constrói a imagem e roda
smoke autenticado. O teste da UI compara transportes com o schema público do SDK.
Lint/typecheck/build e secret scanning também fazem parte do pipeline.

A suíte extensa do core e seu piso de cobertura >=80%, os gates das DAs,
migrations/queries PostgreSQL, RAG/Qdrant e GraphRAG/Neo4j são responsabilidade
[da plataforma](https://github.com/marcos-slima/copilot-platform/blob/main/docs/QUALITY_GATES.md).
Ela testa também commits fixados dos consumidores contra artefatos candidatos.
Isso não equivale a executar os testes fiscais no IIC.

[Comandos locais](GETTING_STARTED.md) · [smoke de imagem](DEPLOY.md).
As evidências antigas de implantação/topologia estão no histórico; os quatro
testes atuais do consumidor não substituem validação de Kyma, conectores ou LLM reais.

# IIC como consumidor

Core e UI vêm de releases de copilot-platform. O código app e a suíte do core
foram preservados no histórico da plataforma; não são copiados neste produto.
A interface iic_copilot.main:app preserva as rotas do SDK e instala sua própria UI.
A versão de produto declarada permanece 1.4.0 como candidato de código:
esta mudança não publica release IIC nem resolve automaticamente as tags 2.x históricas.
As versões compartilhadas são independentes do número do produto.

Gates de arquitetura/quality do core são executados upstream. Este repositório
valida o shell instalado, o contrato de transportes na UI, HTTP e imagem.
A desativação de pipelines antigos decorre dessa transferência de propriedade,
não de remover testes funcionais do core. Leia o inventário da plataforma.

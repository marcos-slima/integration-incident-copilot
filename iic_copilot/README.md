# Shell backend IIC

Entrada ASGI do produto: `uv run uvicorn iic_copilot.main:app --reload`.
Depende do SDK público do pacote `copilot-core`; não importa o produto fiscal.
O shell React continua em `frontend` (`@copilot/iic-ui`). A raiz possui o
manifest de distribuição do IIC e o Dockerfile preserva o produto na porta 8000.

# Executar o IIC como produto independente

Requisitos: Python 3.12+, uv 0.13.4, Node.js 22+ e, no Linux, cmake, make, gcc,
pkg-config, libssl-dev, libffi-dev e libsasl2-dev. Dependências vêm dos locks;
não é necessário clonar a plataforma ou o FISC.

Na raiz deste repositório, reproduza a instalação por wheel usada na CI:

~~~bash
uv sync --locked --python 3.12 --no-install-project
npm ci --prefix frontend --ignore-scripts
npm run build --prefix frontend
IIC_BUILD_REQUIRE_UI=1 uv build --wheel --no-sources --out-dir dist
uv pip install --python .venv/bin/python --no-deps dist/integration_incident_copilot-1.4.0-py3-none-any.whl
uv run --no-sync python -I scripts/quality_gate.py
uv run --no-sync python -I -m pytest -c pyproject.toml -o pythonpath= tests -q
~~~

A instalação `--no-deps` pressupõe dependências já sincronizadas pelo primeiro
comando. Os testes usam o pacote instalado, sem depender de um diretório `app/`.

Copie `.env.example` para `.env` e configure autenticação, usuários e serviços
conforme o deployment. Não use credenciais reais como exemplos versionados.
Depois da instalação acima:

~~~bash
uv run --no-sync uvicorn iic_copilot.main:app --host 127.0.0.1 --port 8000
~~~

Abra `http://127.0.0.1:8000`. A imagem e o wheel servem a UI embutida em
`iic_copilot/_ui`; cookie do produto: `iic_session`.
Para editar o frontend sem reconstruir o wheel, execute `npm run dev --prefix frontend`;
o Vite encaminha as rotas configuradas para a API em 8000. Para servir o build
local diretamente pela API, defina `UI_DIST_DIR=frontend/dist` antes de iniciá-la.

Verifique `/health` para disponibilidade HTTP. Isso não comprova conectividade
com LLM, Qdrant ou conectores. Diagnósticos completos dependem desses serviços.
Veja [deploy](DEPLOY.md) e [uso](USER_GUIDE.md).

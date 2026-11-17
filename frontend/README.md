# UI do IIC — @copilot/iic-ui

Shell de integração do produto. Consome @copilot/core-ui por tarball versionado
em package.json/package-lock.json; não é workspace da plataforma nem depende do FISC.

Na raiz do repositório:

~~~bash
npm ci --prefix frontend --ignore-scripts
npm run dev --prefix frontend
npm run lint --prefix frontend
npm run typecheck --prefix frontend
npm run build --prefix frontend
~~~

Vite usa o proxy configurado em vite.config.ts para a API em 8000. O build gera
frontend/dist; o hook Python o inclui em iic_copilot/_ui. Configure UI_DIST_DIR
para servir o build local quando estiver desenvolvendo sem reconstruir o wheel.
Veja [execução instalada](../docs/GETTING_STARTED.md).

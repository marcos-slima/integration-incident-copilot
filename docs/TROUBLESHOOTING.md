# Troubleshooting do IIC após a separação

| Sintoma | Conferência |
|---|---|
| Falta `app` no checkout | Esperado: é namespace do wheel copilot-core; sincronize uv.lock |
| API iniciada sem UI | Use iic_copilot.main:app; compile frontend e instale o wheel; em dev use UI_DIST_DIR=frontend/dist |
| npm procura workspaces ausentes | Use npm ci --prefix frontend, não o antigo lock da raiz |
| Comando uv pede packages/core ou packages/dfe | É instrução histórica; use o quickstart atual |
| Compose falha por senha ausente | Configure as variáveis exigidas na interpolação, incluindo profiles não iniciados |
| Dashboard não aparece | Baixe operações para .operations; confira o mount do Compose |
| Alembic não acha configuração | Use .operations/alembic.ini no host ou /app/alembic.ini na imagem |
| HTTP responde, mas diagnóstico falha | Verifique separadamente URLs, modelos, Qdrant, credenciais e limites do gateway |
| Import de app interno no produto | Mude o consumidor para app.sdk ou proponha contrato público na plataforma |

[Quickstart](GETTING_STARTED.md) · [Deploy](DEPLOY.md) · [Histórico](history/README.md).
Para investigar implementação de gateway, RAG ou conectores, use o source da
plataforma correspondente à versão instalada; não restaure uma cópia no IIC.

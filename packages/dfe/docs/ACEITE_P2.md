# P2 — aceite técnico local (2026-11-13)

## Resultado

Imagem própria `dfe-copilot:p2-local`, alvo `dfe` do Dockerfile compartilhado,
Compose próprio e workflow `.github/workflows/dfe.yml` implementados. O core e o
produto são instalados por wheels; schemas, catálogo e UI vêm do pacote DF-e.
A base comum não contém shell nem documentos de exemplo do IIC. O alvo `final`
preserva a imagem IIC; não foi criada uma cópia do runtime ou dos componentes.

## Evidências executadas

| Verificação | Resultado |
|---|---|
| Suíte completa do core | 1.977 aprovados, 21 pulados, 17 desmarcados por `not integration` |
| Autenticação e coexistência de cookies | 20 aprovados; login/logout não alteram cookie do outro produto |
| Suíte fiscal contra ambos os wheels fora do checkout | 214 aprovados |
| Build somente de backend | Aprovado sem bundle de UI local |
| Build de entrega sem UI | Recusado como esperado |
| Build imagem DF-e | Aprovado, `PRELOAD_MODELS=0` |
| Smoke da imagem | Aprovado: health, autenticação, cookie próprio, catálogo fiscal, UI/assets, 422 de contexto inválido e logout |
| Recursos no contêiner | XSD válido para fixture sintética, catálogo presente, UI presente, core não editável, usuário não root, shell IIC ausente |
| Compose próprio | Aprovado em projeto temporário com Qdrant e volume próprios; mesmos testes HTTP/recursos e cookie distinto |
| Regressão imagem IIC | Build aprovado; shell importado, cookie histórico e documentos de exemplo preservados |
| Ruff, formatação e quality gate | Sem falhas; avisos informativos de benchmark/baseline permanecem |

## Validação contra base fiscal já existente

O modo `compose.existing.yaml` foi validado sem iniciar Qdrant próprio e sem
executar ingestão/reset: dois casos sintéticos revisados passaram pelo Ollama
real, com `F-kb`, referências de domínio e `cause_status=hypothesis`. A coleção
`dfe_kb` permaneceu com 310 pontos e SHA-256
`842c82b9442cbf171bef529a43e67f0d0ad0f697bd049801feebc277514868a0` antes e
depois. O smoke HTTP da imagem também confirmou login, cookie, catálogo, UI,
XSD e logout nesse modo.

Os contêineres, rede e volume temporários usados pelo smoke foram removidos.
Os serviços existentes do usuário não foram substituídos. Chaves, usuário,
senha e segredo de sessão dos testes foram gerados em memória e arquivo
privado temporário, removido ao fim. Nenhuma credencial real entrou nos testes,
na imagem ou nos commits.

## Limites e próxima etapa externa

O workflow foi validado por comandos equivalentes localmente e pela sintaxe
YAML; ainda não foi executado no GitHub. A próxima etapa externa é publicar a
branch/abrir o PR e conferir os jobs remotos. Não houve push, publicação em
registro, merge, release remoto ou deploy.

Os testes não fizeram inferência com LLM nem ingestão de corpus na nova stack,
nem validaram SEFAZ/SAP. O XML é sintético e o contexto inválido é recusado
antes do diagnóstico. `PRELOAD_MODELS=1` não foi repetido. A stack Compose
possui Qdrant novo e não migra automaticamente dados do ambiente nativo.

## Reprodução

Veja `DEPLOY.md` para credenciais locais, primeiro acesso e ingestão.

```bash
uv run python scripts/test_product_isolation.py
docker build --target dfe --build-arg PRELOAD_MODELS=0 -t dfe-copilot:local .
uv run python scripts/smoke_dfe_image.py dfe-copilot:local
uv run python scripts/smoke_dfe_image.py dfe-copilot:local --compose
```

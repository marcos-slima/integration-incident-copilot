# Referência — `app.rag.ingest`

Indexador da base de conhecimento do Integration Incident Copilot.
Processa arquivos `.md`, `.pdf` e `.epub` e os indexa no Qdrant para uso pelo pipeline RAG.

---

## Sintaxe base

```bash
LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib uv run python -m app.rag.ingest [opções]
```

---

## Argumentos

| Argumento | Tipo | Default | Descrição |
|---|---|---|---|
| `--target` | `incidents` \| `reference` \| `all` | `incidents` | Qual base indexar |
| `--limit` | inteiro | `None` (sem limite) | Processa apenas os N primeiros arquivos |
| `--exclude` | string (repetível) | `[]` | Exclui arquivos pelo nome (substring) |
| `--reset` / `--reset-state` | flag | `False` | Apaga o estado local e reindexa os arquivos; **não** apaga a collection Qdrant |
| `--reset-collection` | flag | `False` | Apaga e recria a collection Qdrant do zero; também limpa o estado local ⚠️ perde todos os dados indexados |

---

## Comandos mais usados

### Indexar base de troubleshooting (padrão)
```bash
uv run python -m app.rag.ingest
```
Processa `data/sample_docs/` — os documentos `.md` de casos de troubleshooting.
Só indexa arquivos novos (não reprocessa o que já está no estado).

### Reiniciar indexação dos incidentes (manter collection)
```bash
uv run python -m app.rag.ingest --target incidents --reset-state
```
Apaga apenas o estado local e reindexa todos os arquivos; os dados já no Qdrant são substituídos.
Use quando os arquivos `.md` mudaram mas o schema de embeddings não mudou.

### Recriar collection de incidentes do zero
```bash
uv run python -m app.rag.ingest --target incidents --reset-collection
```
Apaga e recria a collection `sap_incident_docs` no Qdrant e reindexa tudo do zero.
Use quando mudar o schema de metadata, o modelo de embeddings, ou a configuração de vetores.
⚠️ Todos os dados indexados serão perdidos.

### Indexar biblioteca de referência (PDFs/EPUBs)
```bash
LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib uv run python -m app.rag.ingest --target reference
```
Processa `data/reference_library/` — PDFs e EPUBs técnicos SAP.
Continua de onde parou (usa estado salvo em `data/.ingest_state_reference.json`).

### Recriar collection de referência do zero
```bash
LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib uv run python -m app.rag.ingest --target reference --reset-collection
```
Apaga e recria a collection `sap_reference_library` e reindexa tudo.
⚠️ Com 2.000+ arquivos pode levar várias horas (OCR via pymupdf4llm). Todos os dados indexados serão perdidos.

### Indexar todas as bases
```bash
LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib uv run python -m app.rag.ingest --target all
```
Processa `incidents` e depois `reference` em sequência.

### Indexar apenas os primeiros N arquivos (teste)
```bash
uv run python -m app.rag.ingest --target incidents --limit 3
```
Útil para testar o pipeline sem processar a base inteira.

### Excluir arquivos específicos
```bash
uv run python -m app.rag.ingest --target reference --exclude "Copy" --exclude "z-library"
```
Exclui arquivos cujo nome contenha "Copy" ou "z-library".
`--exclude` pode ser repetido quantas vezes precisar.

### Rodar em background com log
```bash
LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib uv run python -m app.rag.ingest \
  --target reference > ~/ingest_reference.log 2>&1 &
echo "PID: $!"
```

### Monitorar progresso em background
```bash
tail -f ~/ingest_reference.log
```

### Verificar se ainda está rodando
```bash
ps aux | grep "app.rag.ingest" | grep -v grep
```

---

## Scripts de apoio

### Verificar estado da indexação
```bash
python3 - << 'EOF'
import json
from pathlib import Path

for state_file in ['data/.ingest_state_incidents.json', 'data/.ingest_state_reference.json']:
    p = Path(state_file)
    if p.exists():
        state = json.load(open(p))
        print(f"{p.name}: {len(state)} arquivos processados")
    else:
        print(f"{p.name}: não existe (nunca indexado)")

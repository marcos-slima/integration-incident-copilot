# DF-e Copilot

Diagnóstico assistido de incidentes de **documentos fiscais eletrônicos**
(NF-e modelo 55) para operações SAP AMS N2/N3. Produto independente que
reutiliza o **core** do Integration Incident Copilot (gateway LLM, RAG,
evidência, revisão humana, auth, observabilidade) por meio de um plugin de
domínio (DA-94).

> **Estado:** MVP em construção, dados sintéticos, acesso read-only. Sem
> homologação fiscal, sem validação em SAP real. Ver `docs/PENDENCIAS.md`.

## Princípio

O LLM explica e prioriza hipóteses. Ferramentas determinísticas decidem o
que é fato: código de retorno (`cStat`), conformidade XSD, dígito da chave de
acesso e correlação por identificador explícito. Nenhuma ação fiscal é
automática.

## Como rodar

```bash
# na raiz do monorepo
uv run uvicorn dfe_copilot.main:app --reload     # API do core com o plugin DF-e
(cd packages/dfe && uv run pytest -q)              # testes do produto (config própria)
(cd packages/dfe && uv run python -m dfe_copilot.kb ingest)   # base fiscal no Qdrant (dfe_kb)
```

Requisição mínima (`POST /diagnose`):

```json
{
  "description": "NF-e rejeitada por duplicidade apos timeout",
  "payload": "<protNFe ...>...</protNFe>",
  "domain_context": {
    "environment": "homologation",
    "jurisdiction": "SP",
    "events": [
      {"evidence_id": "EV-A", "document_ref": "<chave>", "at": "2026-11-11T12:59:00Z", "event": "sent"},
      {"evidence_id": "EV-T", "document_ref": "<chave>", "at": "2026-11-11T13:00:00Z", "event": "timeout"},
      {"evidence_id": "EV-B", "document_ref": "<chave>", "at": "2026-11-11T13:00:01Z", "event": "sent"}
    ]
  }
}
```

A resposta traz `findings` (fatos das ferramentas + hipóteses) e
`cause_status`; `confirmed` só existe depois de revisão humana.

## Estrutura

| Caminho | Conteúdo |
|---|---|
| `src/dfe_copilot/plugin.py` | Plugin de domínio carregado pelo core (`PLUGIN`) |
| `src/dfe_copilot/context.py` | Contrato `FiscalContext` (NF-e 55, leiaute 4.00, ambiente, UF, eventos) |
| `src/dfe_copilot/fiscal/xml_tools.py` | Parse seguro, chave de acesso, `cStat`, XSD offline |
| `src/dfe_copilot/fiscal/correlation.py` | Linha do tempo UTC, regras cStat 204 e timeout |
| `src/dfe_copilot/main.py` | Ponto de entrada da API do produto |
| `src/dfe_copilot/kb.py` | Base de conhecimento fiscal: ingestão e busca híbrida filtrada (`dfe_kb`) |
| `data/schemas/nfe/` | Pacote oficial PL 010g v1.03 + manifesto com hashes |
| `tests/` | Testes do produto e fixture de controle positivo |
| `docs/` | Arquitetura, decisões, pendências e estudo de origem |

## Documentação

- `docs/ARCHITECTURE.md` — arquitetura do produto
- `docs/DECISOES.md` — decisões do domínio fiscal
- `docs/PENDENCIAS.md` — o que depende de ação humana ou infraestrutura
- `docs/ESTUDO_ORIGEM.md` — estudo IIC → SAP AMS/DF-e (v3)

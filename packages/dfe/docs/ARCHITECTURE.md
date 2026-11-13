# DF-e Copilot — Arquitetura

## Visão

```mermaid
%%{init: {'theme':'base','themeVariables':{'background':'#ffffff','primaryColor':'#eef4ff','lineColor':'#555555','fontSize':'13px'}}}%%
flowchart LR
  subgraph PROD[DF-e Copilot · packages/dfe]
    MAIN[main.py<br/>entrada]
    PLG[plugin.py]
    CTX[context.py]
    XML[xml_tools]
    COR[correlation]
    XSD[(PL 010g<br/>+ hashes)]
  end
  subgraph CORE[Core · packages/core/src/app/]
    API[/diagnose]
    DP[domain_plugins]
    GR[grafo LangGraph]
    FD[findings]
  end
  MAIN --> API
  API --> DP
  DP --> PLG
  PLG --> CTX
  PLG --> XML
  PLG --> COR
  XML --- XSD
  GR --> FD
  classDef p fill:#fff6e6,stroke:#d08a00,color:#1a1a1a;
  classDef c fill:#eef4ff,stroke:#3b6fd8,color:#1a1a1a;
  class MAIN,PLG,CTX,XML,COR,XSD p;
  class API,DP,GR,FD c;
  style PROD fill:#fffbf2,stroke:#e8c27a
  style CORE fill:#fafcff,stroke:#9db7ec
```

## Fluxo no grafo

Com `DOMAIN_PLUGINS=dfe_copilot.plugin`, o core insere `domain_tools` entre
`connector` e `retrieve`. O plugin:

1. valida `domain_context` como `FiscalContext` (antes do grafo; recusa = 422);
2. analisa o XML em `payload` sobre os **bytes originais** (hash SHA-256),
   sem DTD/entidades/rede, e valida no XSD quando há entrypoint aplicável;
3. normaliza os eventos para UTC e aplica as regras:
   - retorno com `cStat 204` → fato `F-204`; hipótese `H1` só com ≥ 2 envios
     do mesmo documento **e** timeout entre eles;
   - sem retorno e com timeout → `processing_outcome=unknown`, hipótese de
     resposta perdida (nunca "SEFAZ indisponível");
4. com `DFE_KB_ENABLED=true` (ligado por padrão em `dfe_copilot.main`),
   consulta a base fiscal `dfe_kb` pelo cStat do retorno: trechos aplicáveis
   viram o fato `F-kb` (`source=rule_reference`, `evidence_refs` com
   `kb:arquivo#pNcM` e `rule_ref`, ex. `2B08-20`); base indisponível ou sem
   trecho vira a lacuna `G-kb`;
5. devolve `domain_findings`; o core junta com a causa proposta pelo LLM
   (sempre `hypothesis`) em `DiagnosisResponse.findings`.

Lacunas viram fatos explícitos: `G-noxml`, `G-xml`, `G-events`, `G-ref`,
`G-kb`, `G-sig`, `G-lote`, `G-ns`, `G-539`. Fatos de XML: `F-amb`, `F-cStat` (com texto oficial do MOC 7.0 Anexo I), `F-prot`, `F-dv`, `F-xsd`, `F-sig`, `F-sigcnpj`, `F-sigvalid`, `F-protkey`.

## Estado por componente

| Componente | Estado |
|---|---|
| Contrato `FiscalContext` | Implementado (NF-e 55, leiaute 4.00) |
| XML seguro, chave, `cStat`, XSD PL 010g | Implementado; protocolo (`protNFe`, `nfeProc`, `retEnviNFe`, `retConsReciNFe`) validado por entrypoint local sobre o tipo oficial (D3) |
| Assinatura XMLDSig | Implementado, perfil fechado NF-e; integridade sim, confiança do certificado não (D4) |
| Regras 204 (H1), 539 (H539) e timeout | Implementado; 539 e borda `<=` do H1 vêm da revisão P7 (D6) |
| Plugin no grafo do core | Implementado (`domain_tools`) |
| Corpus e base de conhecimento | `data/corpus/` (NT 2018.005 + manifesto + regra 2B08-20) e `kb.py`: coleção híbrida `dfe_kb`, filtros de modelo/cStat/status antes da busca, abstenção sem fonte; ingestão real pendente de execução local (P3) |
| UI de fatos × hipóteses | Tipos e rótulos no core; tela dedicada pendente |
| Dataset e regressão | 104 casos sintéticos v1 (8 famílias, split dev/validation/blind, todos aprovados na revisão P7; especialista fiscal pendente, P11) + regressão offline (`tests/test_regressao_dataset.py`); revisão fiscal pendente (P7, `docs/REVISAO_DATASET.md`) |
| Integração SAP / SEFAZ | Fora do MVP (read-only, por contrato) |

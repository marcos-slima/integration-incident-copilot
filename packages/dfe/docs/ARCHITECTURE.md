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
  subgraph CORE[Core · app/]
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
4. devolve `domain_findings`; o core junta com a causa proposta pelo LLM
   (sempre `hypothesis`) em `DiagnosisResponse.findings`.

Lacunas viram fatos explícitos: `G-noxml`, `G-xml`, `G-events`, `G-ref`,
`G-amb`.

## Estado por componente

| Componente | Estado |
|---|---|
| Contrato `FiscalContext` | Implementado (NF-e 55, leiaute 4.00) |
| XML seguro, chave, `cStat`, XSD PL 010g | Implementado; protocolo sem XSD (pacote não traz `protNFe`) |
| Regras 204 e timeout | Implementado |
| Plugin no grafo do core | Implementado (`domain_tools`) |
| Coleção RAG fiscal | Pendente (`PENDENCIAS.md`) |
| UI de fatos × hipóteses | Tipos e rótulos no core; tela dedicada pendente |
| Dataset de avaliação | Gerador sintético mínimo; curadoria pendente |
| Integração SAP / SEFAZ | Fora do MVP (read-only, por contrato) |

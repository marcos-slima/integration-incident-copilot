# Cassettes de conectores

Avaliação externa (médio prazo, item 7): "Testes de contrato dos
conectores contra respostas reais documentadas (VCR/cassettes)".

Cada arquivo `.json` aqui é o corpo de uma resposta HTTP real de um
conector, no formato documentado publicamente pela API do respectivo
sistema (não inventado ad-hoc dentro do código do teste) — o campo
`_source` de cada cassette explica de qual endpoint/documentação o
formato veio. `tests/cassette_loader.py::load_cassette()` lê o arquivo e
devolve só o campo `response` (o corpo JSON de verdade, sem o
metadado `_source`), que os testes em `tests/test_connectors.py` e
`tests/test_cap_connector.py` usam como corpo da resposta simulada
via `httpx.MockTransport`.

**Fora de escopo aqui (deliberado):** o conector de API Management
(`app/connectors/apimanagement_connector.py`) não tem cassette — seu
próprio módulo já se autodocumenta como schema especulativo (nunca
validado contra uma instância SAP API Management real), e corrigir
isso é o item de **longo prazo** "Validação real do API Management
connector e correção do schema especulativo" da mesma avaliação
externa, ainda não autorizado. O conector RFC também fica de fora —
depende do SDK proprietário `pyrfc`, não instalado/testável neste
ambiente (mesmo motivo pelo qual ele já é excluído do circuit
breaker, ver app/connectors/base.py).

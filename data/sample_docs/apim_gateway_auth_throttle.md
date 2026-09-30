# SAP API Management (API Gateway) - 401/403, 429 e 55s

## Sintoma
Chamadas que passam pela SAP API Management / API Gateway do BTP
falham com 401/403 (credencial/OAuth2), 429 (throttling) oucortam em
~55 segundos (timeout). Relatos tipicos: "API retorna 401 no gateway",
"429 too many requests na API", "timeout de 55 segundos na SAP API
Management", "cliente consumindo API Management falha auth". Diferente
de um 401 direto no SAP (ver cpi_http_401.md), aqui o erro vem da
camada de API Management que esta na frente do backend.

## Causas comuns
- Token OAuth2 expirado: SAP API Management exige access token com
  expiracao entre 180 segundos e 30 dias; token de servico expirado
  ou scope insuficiente gera 401/403
- Limite de throttling/rate limit atingido -> 429 (o backend ainda
  esta saudavel; quem barrou foi o proprio gateway)
- Timeout de 55 segundos do proxy: o backend precisa responder em ate
  ~55s, senao a chamada e cortada com timeout
- Certificado do Keystore expirado ou nome de certificado com mais de
  25 caracteres (rotacao de credencial pendente)
- TLS/SNI: cliente sem SNI recebe certificado padrao e falha o
  handshake TLS 1.2/1.3

## Diagnostico
1. Separar a camada: testar o backend diretamente (sem passar pelo
   gateway) para saber se o 401/timeout vem do gateway ou do SAP
2. Inspecionar os headers de resposta: 401 vs 403 aponta token/scope;
   429 com headers de rate-limit confirma throttling
3. Para timeout, medir o tempo de resposta real do backend; se passar
   de ~55s, o problema e o limite do proxy, nao a latencia da rede
4. Verificar validade do certificado no Keystore e o tamanho do nome

## Resolucao tipica
Renovar o token OAuth2 (client credentials) com expiracao dentro da
faixa 180s-30d e escopos corretos; ajustar o plano de throttling para
elevar o limite quando o 429 for volume legitimo; reduzir o tempo de
resposta do backend ou mover o processamento para fluxo assincrono
para caber no limite de 55s; rotacionar o certificado no Keystore
mantendo o nome com ate 25 caracteres.

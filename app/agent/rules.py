"""DA-33: Rule Engine determinístico para erros SAP/integração conhecidos.

Camada zero de custo — avaliada ANTES de qualquer chamada ao LLM.
Se um padrão regex bate com a descrição do incidente (ou com a
mensagem do conector, se disponível), devolve um DiagnosisResponse
completo diretamente, sem consumir tokens.

Extensível: adicione entradas em KNOWN_ERROR_RULES. Cada regra tem:
- patterns: lista de regex (re.IGNORECASE aplicado)
- probable_root_cause: causa raiz determinística
- confidence: fixo em 0.90 — alta certeza por pattern matching
- next_steps: ações concretas para o operador
- category: agrupamento para logs/métricas
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Catálogo de regras
# ---------------------------------------------------------------------------


@dataclass
class ErrorRule:
    patterns: list[str]
    probable_root_cause: str
    next_steps: list[str]
    category: str
    confidence: float = 0.90
    _compiled: list[re.Pattern] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self.patterns]

    def matches(self, text: str) -> bool:
        return any(p.search(text) for p in self._compiled)


# Regras ordenadas por especificidade — mais específicas primeiro.
# Cada regra representa uma classe de erro SAP/integração determinístico
# (60–70% dos incidentes reais, conforme revisão arquitetural externa).
KNOWN_ERROR_RULES: list[ErrorRule] = [
    # ------------------------------------------------------------------
    # OAuth / Autenticação
    # ------------------------------------------------------------------
    ErrorRule(
        patterns=[
            r"oauth.*token.*expir",
            r"token.*expir",
            r"401.*unauthorized",
            r"access.?token.*invalid",
            r"JWT.*expir",
        ],
        category="auth_oauth_expired",
        probable_root_cause=(
            "Token OAuth2 expirado ou inválido. O sistema de origem não conseguiu "
            "autenticar na API de destino."
        ),
        next_steps=[
            "Renovar o token OAuth2 (verificar TTL e refresh_token no sistema de origem).",
            "Confirmar que as credenciais client_id/client_secret estão atualizadas no conector.",
            "Verificar se o IdP (Identity Provider) está acessível e respondendo.",
            "Se o erro for recorrente, revisar a lógica de refresh automático.",
        ],
    ),
    ErrorRule(
        patterns=[
            r"403.*forbidden",
            r"authorization.*failed",
            r"authorization.*error",
            r"insuficient.*authoriz",
            r"no.*authoriz.*object",
            r"SY-SUBRC.*4.*authoriz",
        ],
        category="auth_forbidden",
        probable_root_cause=(
            "Erro de autorização (403 Forbidden). O usuário técnico ou a role não "
            "possui as permissões necessárias para executar a operação."
        ),
        next_steps=[
            "Verificar os objetos de autorização SAP (SU53 / ST01) para o usuário técnico.",
            "Confirmar que o usuário técnico tem a role/profile adequado no sistema de destino.",
            "Revisar as configurações de autorização na API (scopes OAuth2, ACLs).",
        ],
    ),
    # ------------------------------------------------------------------
    # Material / Estoque / MRP
    # ------------------------------------------------------------------
    ErrorRule(
        patterns=[
            r"M8082",
            r"material.*lock",
            r"material.*bloqueado",
            r"ENQUEUE.*material",
        ],
        category="sap_material_lock",
        probable_root_cause=(
            "Material bloqueado (lock) por outro processo em execução (M8082). "
            "Conflito de acesso simultâneo ao registro de material."
        ),
        next_steps=[
            "Aguardar 15 minutos e tentar o reprocessamento.",
            "Verificar transação SM12 (SAP Lock Monitor) para identificar o processo bloqueador.",
            "Confirmar se há um job batch em execução no mesmo material.",
            "Se o lock for antigo (> 1h), verificar com o administrador SAP se pode ser liberado.",
        ],
    ),
    ErrorRule(
        patterns=[
            r"VK041",
            r"condition.*record.*missing",
            r"condicao.*preco.*ausente",
            r"no.*pricing.*condition",
            r"MM.*price.*not.*found",
        ],
        category="sap_pricing_condition_missing",
        probable_root_cause=(
            "Registro de condição de preço ausente (VK041). "
            "A combinação material/cliente/organização de vendas não tem condição de preço vigente."
        ),
        next_steps=[
            "Verificar transação VK13 (Exibir Condições) para a combinação afetada.",
            "Criar ou atualizar o registro de condição via VK11/VK12.",
            "Confirmar se a data de validade da condição está vigente.",
            "Verificar se a determinação de preços (pricing procedure) está correta no pedido.",
        ],
    ),
    # ------------------------------------------------------------------
    # IDoc
    # ------------------------------------------------------------------
    ErrorRule(
        patterns=[
            r"IDoc.*status.*51",
            r"status.*51.*IDoc",
            r"IDOC.*APPLICATION.*ERROR",
            r"WE19.*status.*51",
        ],
        category="sap_idoc_status_51",
        probable_root_cause=(
            "IDoc com status 51 (Application Document Not Posted). "
            "O IDoc chegou ao sistema de destino mas falhou na posting (erro de aplicação)."
        ),
        next_steps=[
            "Analisar o IDoc em WE02/WE05 para identificar a mensagem de erro específica.",
            "Verificar SE16N na tabela EDID4 (segmentos do IDoc) para dados inválidos.",
            "Corrigir a causa raiz do erro de aplicação e reprocessar via BD87 ou WE19.",
            "Se for erro recorrente, revisar o mapeamento de campos no parceiro de comunicação.",
        ],
    ),
    ErrorRule(
        patterns=[
            r"IDoc.*status.*26",
            r"status.*26.*IDoc",
            r"IDOC.*ALE.*ERROR",
        ],
        category="sap_idoc_status_26",
        probable_root_cause=(
            "IDoc com status 26 (Error During Syntax Check). "
            "O IDoc não passou na verificação de sintaxe — campos obrigatórios ausentes ou inválidos."
        ),
        next_steps=[
            "Verificar WE02/WE05 para os segmentos com erro de sintaxe.",
            "Revisar o mapeamento de campos no iFlow/interface de origem.",
            "Confirmar a estrutura do IDoc contra a definição em WE30/WE31.",
        ],
    ),
    # ------------------------------------------------------------------
    # HTTP / Conectividade
    # ------------------------------------------------------------------
    ErrorRule(
        patterns=[
            r"HTTP.*503",
            r"503.*Service.*Unavailable",
            r"service.*unavailable",
            r"backend.*unavailable",
            r"system.*unavailable",
        ],
        category="http_503_unavailable",
        probable_root_cause=(
            "Serviço de destino indisponível (HTTP 503). "
            "O backend está fora do ar, em manutenção ou sobrecarregado."
        ),
        next_steps=[
            "Verificar o status do sistema de destino (health check / portal de status).",
            "Aguardar e tentar reprocessar após 5–15 minutos.",
            "Confirmar se há janela de manutenção programada.",
            "Se persistir, escalar para o time de infraestrutura do sistema de destino.",
        ],
    ),
    ErrorRule(
        patterns=[
            r"HTTP.*504",
            r"504.*Gateway.*Timeout",
            r"connection.*timeout",
            r"read.*timeout",
            r"socket.*timeout",
        ],
        category="http_timeout",
        probable_root_cause=(
            "Timeout de conexão ou de leitura. O sistema de destino não respondeu "
            "dentro do tempo limite configurado."
        ),
        next_steps=[
            "Verificar a carga do sistema de destino (pode estar lento por alto volume).",
            "Aumentar o timeout no conector/iFlow se o processamento for longo por natureza.",
            "Verificar a rede entre os sistemas (latência, firewall).",
            "Reprocessar o incidente em horário de menor carga.",
        ],
    ),
    ErrorRule(
        patterns=[
            r"connection.*refused",
            r"ECONNREFUSED",
            r"unable.*connect",
            r"host.*unreachable",
            r"network.*unreachable",
            r"no.*route.*to.*host",
        ],
        category="network_connection_refused",
        probable_root_cause=(
            "Conexão recusada ou host inacessível. O sistema de destino não está "
            "aceitando conexões na porta/endereço configurado."
        ),
        next_steps=[
            "Verificar se o host/IP e porta do destino estão corretos no conector.",
            "Confirmar que o sistema de destino está em execução.",
            "Verificar regras de firewall entre os sistemas.",
            "Testar conectividade via ping/telnet do host de origem.",
        ],
    ),
    # ------------------------------------------------------------------
    # CPI / Integration Suite
    # ------------------------------------------------------------------
    ErrorRule(
        patterns=[
            r"MAPPING_FAIL",
            r"mapping.*failed",
            r"xslt.*error",
            r"XSLT_PARS",
            r"groovy.*script.*error",
            r"script.*exception.*iflow",
        ],
        category="cpi_mapping_error",
        probable_root_cause=(
            "Erro no mapeamento ou script do iFlow (XSLT/Groovy). "
            "O payload de entrada não corresponde ao schema esperado ou há um bug no script."
        ),
        next_steps=[
            "Analisar o Message Processing Log (MPL) no Integration Suite para o passo que falhou.",
            "Verificar o payload de entrada contra o schema esperado pelo mapeamento.",
            "Revisar o script Groovy/XSLT para tratamento de campos nulos ou opcionais.",
            "Testar o mapeamento com o payload problemático via Message Mapping Simulation.",
        ],
    ),
    ErrorRule(
        patterns=[
            r"certificate.*expired",
            r"SSL.*expired",
            r"certificado.*expirado",
            r"PKIX.*path.*build.*failed",
            r"unable.*find.*valid.*certification",
            r"SSL.*handshake.*fail",
        ],
        category="ssl_certificate_expired",
        probable_root_cause=(
            "Certificado SSL/TLS expirado ou inválido. A conexão segura não pode ser "
            "estabelecida porque o certificado do servidor ou do cliente expirou."
        ),
        next_steps=[
            "Verificar a data de validade do certificado no sistema de destino.",
            "Renovar o certificado e atualizar no keystore do Integration Suite (Storemanager).",
            "Se for certificado de cliente, atualizar também no sistema de destino.",
            "Após a renovação, testar a conexão via Connection Test no conector.",
        ],
    ),
    # ------------------------------------------------------------------
    # RFC / BAPI
    # ------------------------------------------------------------------
    ErrorRule(
        patterns=[
            r"RFC.*destination.*not.*found",
            r"destination.*not.*found",
            r"RFC.*logon.*failed",
            r"RFC.*connection.*failed",
            r"ABAP.*RFC.*error",
        ],
        category="rfc_destination_error",
        probable_root_cause=(
            "Destino RFC não encontrado ou falha de logon RFC. "
            "A configuração do destino RFC está incorreta ou o sistema ABAP de destino está inacessível."
        ),
        next_steps=[
            "Verificar a configuração do destino RFC na transação SM59.",
            "Executar o Connection Test e Authorization Test no destino RFC (SM59).",
            "Confirmar que o usuário de background do RFC tem as autorizações necessárias.",
            "Verificar se o sistema ABAP de destino está em execução (SM21, ST22).",
        ],
    ),
    # ------------------------------------------------------------------
    # Rate limit / Quota
    # ------------------------------------------------------------------
    ErrorRule(
        patterns=[
            r"429.*too.*many.*request",
            r"too.*many.*request",
            r"rate.*limit.*exceeded",
            r"quota.*exceeded",
            r"throttl",
        ],
        category="rate_limit_exceeded",
        probable_root_cause=(
            "Limite de requisições excedido (HTTP 429 / Rate Limit). "
            "O volume de chamadas ultrapassou a cota configurada na API de destino."
        ),
        next_steps=[
            "Aguardar o período de reset do rate limit (verificar cabeçalho Retry-After).",
            "Implementar backoff exponencial nas tentativas de reprocessamento.",
            "Revisar a frequência de chamadas no iFlow/integração de origem.",
            "Solicitar aumento de quota ao fornecedor da API, se necessário.",
        ],
    ),
    # ------------------------------------------------------------------
    # Duplicidade / Idempotência
    # ------------------------------------------------------------------
    ErrorRule(
        patterns=[
            r"duplicate.*entry",
            r"unique.*constraint",
            r"already.*exist",
            r"ja.*existe",
            r"document.*already.*posted",
            r"FI.*duplicate.*document",
        ],
        category="duplicate_document",
        probable_root_cause=(
            "Tentativa de criação de documento duplicado. "
            "O registro já existe no sistema de destino (violação de constraint de unicidade)."
        ),
        next_steps=[
            "Verificar se o documento já foi criado em uma tentativa anterior (buscar pelo identificador).",
            "Revisar a lógica de idempotência no iFlow/integração (controle de reenvio).",
            "Se o documento existente estiver correto, marcar o incidente como resolvido sem reprocessamento.",
            "Se o documento existente estiver incorreto, reverter/estornar antes de reprocessar.",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Função principal — chamada por _run_diagnosis_agent antes do LLM
# ---------------------------------------------------------------------------


def match_known_error(text: str) -> dict | None:
    """Avalia o texto contra todas as regras conhecidas.

    Retorna um dict compatível com DiagnosisModel se alguma regra bater,
    ou None se nenhuma bater (e o pipeline deve seguir para o LLM).

    Args:
        text: Texto combinado (description + mensagem do conector, se houver).
    """
    if not text:
        return None

    for rule in KNOWN_ERROR_RULES:
        if rule.matches(text):
            _logger.info(
                "[rule_engine] Incidente resolvido deterministicamente — categoria=%s",
                rule.category,
            )
            return {
                "matched_source": f"rule_engine:{rule.category}",
                "probable_root_cause": rule.probable_root_cause,
                "confidence": rule.confidence,
                "next_steps": rule.next_steps,
                "rule_engine_category": rule.category,
                "llm_provider_used": "rule_engine",
                "evidence_strength": 0.95,
            }
    return None

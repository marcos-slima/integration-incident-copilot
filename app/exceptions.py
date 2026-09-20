"""Excecoes compartilhadas do SAP Integration Copilot.

Concentradas aqui (em vez de cada modulo inventar a sua) para que
codigo de chamada possa fazer `except ConfigurationError` de forma
previsivel, independente de qual componente (LLM provider, conector)
levantou o erro.
"""


class ConfigurationError(Exception):
    """Configuracao ausente ou invalida para operar em modo real
    (ex: provedor de LLM sem API key, conector sem instancia
    configurada quando use_real=True). Nao se aplica ao modo demo/mock,
    que e o default e funciona sem nenhuma configuracao externa.
    """


class DiagnosisTimeoutError(TimeoutError):
    """Levantada por run_diagnosis (app/agent/graph.py) quando o
    pipeline completo (retrieval + GraphRAG + LLM + relatorio) excede
    settings.diagnosis_timeout_seconds - avaliacao externa (curto
    prazo, item 4). Distinta de um timeout de UMA chamada LLM
    (app/llm/factory.py::get_chat_model, cfg.llm_request_timeout_seconds) -
    essa protege contra a SOMA de varias etapas lentas, nao so uma
    travada."""

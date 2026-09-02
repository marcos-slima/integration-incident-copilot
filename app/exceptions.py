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

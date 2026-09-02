"""LLM Gateway do SAP Integration Copilot.

Ver docs/ARCHITECTURE.md e a Decisao de Arquitetura #10 no README
para o raciocinio por tras deste modulo.
"""

from app.llm.factory import get_chat_model

__all__ = ["get_chat_model"]

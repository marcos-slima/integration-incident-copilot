"""Contratos públicos: mudanças incompatíveis exigem nova versão major."""

import re

HTTP_CONTRACT_VERSION = "1.0"
PLUGIN_CONTRACT_VERSION = "1.1"
SDK_CONTRACT_VERSION = "1.0"


def compatible_version(required: str, supported: str) -> bool:
    """Mesmo major e minor requerido não superior ao suportado."""
    if not isinstance(required, str) or not re.fullmatch(r"[1-9][0-9]*\.[0-9]+", required):
        return False
    try:
        major, minor = (int(part) for part in required.split("."))
        supported_major, supported_minor = (int(part) for part in supported.split("."))
    except (ValueError, AttributeError):
        return False
    return major >= 1 and minor >= 0 and major == supported_major and minor <= supported_minor

"""Product fixtures use only the public SDK testing contract."""

import os
import secrets

os.environ["API_KEY"] = secrets.token_urlsafe(32)


from app.sdk.pytest_plugin import (  # noqa: F401
    _isolate_auth_guard,
    _isolate_event_idempotency,
    _isolate_oauth_token_cache,
    _reset_auth_guard,
    _reset_connector_circuit_breaker,
    _reset_rate_limiter,
)

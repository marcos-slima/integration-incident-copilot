# Rate Limiting — DA-03

## Overview
The API implements rate limiting to prevent abuse (brute force, DoS). The implementation uses **SlowAPI** with Redis backend (with in-memory fallback when Redis is unavailable).

## Configuration

### Rate Limit Threshold
- **Default**: 1000 requests per second (RPS) per client
- **Bucket key**: `IP:endpoint` (not API key)

### Endpoint-Specific Limits
Auth endpoints (`/auth/login`, `/auth/verify/email`, `/auth/verify/phone`) use **username+IP hybrid** to prevent:
- Password brute force via different API keys
- User enumeration (timing attacks reveal user existence)

### Key Functions
- `app/rate_limit.py:64` — `request_client_identity_for_auth_endpoints()` — identifies clients for sensitive endpoints
- `app/rate_limit.py:98` — `limiter` singleton — applied via `SlowAPIMiddleware` in `app/main.py:104`

### Fallback Behavior
When Redis is unavailable:
- Falls back to in-memory storage
- Rate limits still enforced (per-process only — not distributed)
- No errors raised; requests rate-limited based on available data

## Testing

### Manual Test
```bash
# Test rate limiting (1000 RPS threshold)
ab -n 1010 -c 10 http://localhost:8000/diagnose -H "X-API-Key: test" -H "Content-Type: application/json" -d '{"incident":"test"}'
```

### Expected Behavior
- First ~1000 requests succeed
- Remaining requests return `429 Too Many Requests`

## References
- **DA-03**: Rate limit policy
- `app/rate_limit.py` — implementation
- `app/main.py:95-120` — middleware setup
- SlowAPI docs: https://slowapi.readthedocs.io/

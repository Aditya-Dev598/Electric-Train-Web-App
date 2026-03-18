# Security Report

## Implemented Security Controls

### 1. Input Validation (OWASP A03: Injection)
- **Station name**: Regex-validated, only alphanumeric + safe chars allowed
- **Operator code**: 2-3 uppercase letters only, optional allowlist enforcement
- **Dates**: Strict YYYY-MM-DD format, parsed with datetime (no eval)
- **Route names**: Alphanumeric + limited special chars, max 200 chars
- **All inputs**: Server-side validation (never trust client)

### 2. CSV Injection Protection (OWASP A03: Injection)
- All CSV cell values checked for dangerous prefixes: `=`, `+`, `-`, `@`, `\t`, `\r`
- Dangerous values prefixed with single quote `'` to neutralize formula execution
- Applied at export time in `csv_exporter.py`

### 3. Rate Limiting (OWASP A04: Insecure Design)
- Token bucket rate limiter: configurable requests per minute per IP
- Returns HTTP 429 when exceeded
- Note: For production, replace with Redis-based rate limiting

### 4. Request Size Limits
- Configurable max request body size (default 1MB)
- Returns HTTP 413 for oversized requests

### 5. CORS (OWASP A05: Security Misconfiguration)
- Strict origin allowlist via `CORS_ORIGINS` env var
- Only GET and POST methods allowed
- Credentials not allowed by default
- Max-age: 3600s

### 6. Security Headers
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Cache-Control: no-store`
- `Content-Security-Policy: default-src 'self'`

### 7. SSRF Protection
- Backend only makes requests to configured Darwin API URL
- No user-controlled URLs are used in backend HTTP requests
- Darwin URL hardcoded in config (env var only)

### 8. Path Traversal Protection
- `sanitize_for_path()` removes `..`, `/`, `\`, null bytes
- Download endpoints use UUID-based cache keys, not file paths

### 9. No Shell Execution
- No `subprocess`, `os.system`, or `exec` calls with user input
- All processing is pure Python

### 10. Credential Security
- All API tokens via environment variables
- No hardcoded credentials
- Darwin token not logged
- `.env.example` provided with placeholder values

## Recommended Security Scans

```bash
# Python security linting
bandit -r backend/app/ -f json -o bandit_report.json

# Dependency vulnerability audit
pip-audit -r backend/requirements.txt

# Static analysis
semgrep --config=p/python backend/app/

# OWASP ZAP baseline (requires running app)
# docker run -t owasp/zap2docker-stable zap-baseline.py -t http://localhost:8000
```

## Known Limitations

1. **In-memory rate limiting**: Not shared across workers; use Redis for production
2. **In-memory result cache**: No TTL/eviction; use Redis for production
3. **No authentication**: API is open; add auth layer for production deployment
4. **Darwin SOAP XML parsing**: Uses ElementTree (not lxml); safe for read-only parsing
5. **CIF file loading at startup**: Large files may slow startup; consider background loading

## Dependency Pinning

All dependencies are pinned to specific versions in `requirements.txt`:
- `fastapi==0.115.6`
- `uvicorn==0.34.0`
- `pydantic==2.10.4`
- `requests==2.32.3`
- `python-dotenv==1.0.1`

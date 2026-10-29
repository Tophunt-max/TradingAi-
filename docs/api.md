# API (v1), Phase 1

The full machine-readable spec is in [`openapi.json`](./openapi.json). Interactive docs are
served at `http://localhost:8000/docs` in development and test only.

## Conventions

- Base path `/api/v1`. JSON request and response bodies.
- Auth: `Authorization: Bearer <access_token>`. Access tokens last 15 minutes. Use
  `/auth/refresh` to rotate them.
- Every error uses one envelope:
  ```json
  { "error": { "code": "INVALID_CREDENTIALS", "message": "Invalid credentials.", "details": null, "request_id": "9f1c..." } }
  ```
- `X-Request-ID` is echoed (or generated) on every response.
- List endpoints take `limit` (1–100, default 20) and `offset`, and return
  `{ items, total, limit, offset }`.
- Timestamps are ISO-8601 UTC.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health/live` | – | Liveness |
| GET | `/health/ready` | – | DB / Redis checks. Workers and broker report `not_configured` |
| GET | `/system/status` | – | Environment, data mode, live-trading blockers |
| POST | `/auth/register` | – | Email or phone + password + risk acknowledgement. Sends OTP |
| POST | `/auth/verify` | – | OTP → token pair |
| POST | `/auth/verify/resend` | – | Always 202 |
| POST | `/auth/login` | – | 403 `VERIFICATION_REQUIRED`, 429 `ACCOUNT_LOCKED` |
| POST | `/auth/refresh` | – | Rotates the refresh token. Re-use revokes the session |
| POST | `/auth/logout` | ✓ | Revokes the current session |
| POST | `/auth/password/forgot` | – | Always 202 (enumeration-safe) |
| POST | `/auth/password/reset` | – | Revokes all sessions |
| GET/PATCH | `/users/me` | ✓ | Profile |
| GET/PATCH | `/users/me/preferences` | ✓ | Theme, language, motion, haptics, notifications |
| POST | `/users/me/password` | ✓ | Signs out other sessions |
| GET | `/users/me/sessions` | ✓ | Active devices |
| DELETE | `/users/me/sessions/{id}` | ✓ | Remote sign-out |
| GET | `/users/me/audit-logs` | ✓ | Own audit history |
| GET | `/admin/audit-logs` | admin | Filter by `user_id`, `action` |
| GET | `/market/indices` | ✓ | **DEMO** synthetic data only |

## Example requests

```bash
API=http://localhost:8000/api/v1

curl -s -X POST $API/auth/register -H 'Content-Type: application/json' -d '{
  "full_name": "Asha Trader", "email": "asha@example.com",
  "password": "Str0ngPassw0rd!", "accept_risk_disclosure": true }'

# In development the 6-digit code is printed in the API log ("DEV ONLY verification code ...").
curl -s -X POST $API/auth/verify -H 'Content-Type: application/json' \
  -d '{"identifier": "asha@example.com", "code": "123456"}'

curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"identifier": "asha@example.com", "password": "Str0ngPassw0rd!"}'

TOKEN=...   # access_token from the response
curl -s $API/market/indices -H "Authorization: Bearer $TOKEN"
# => {"source":"DEMO","is_demo":true,"is_live":false,"as_of":"...","disclaimer":"DEMO data: ...","items":[...]}

curl -s -X POST $API/auth/refresh -H 'Content-Type: application/json' \
  -d '{"refresh_token": "..."}'
```

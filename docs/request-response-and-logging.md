# Request, Response, and Logging Schema

This document describes the **HTTP contract** for `POST /v1/rerank` and the **structured JSON logs** emitted by the gateway when `LOG_JSON=true` (default in `.env.example`).

Implementation references:

- Handler: `app/api/rerank.py`
- Request body model: `app/models/schemas.py`
- Conversation id resolution: `app/core/conversation.py`
- Log formatter: `app/core/logging.py`

---

## `POST /v1/rerank`

### Correlation headers (optional)

`X-Request-Id`, `X-Trace-Id`, and `X-Session-Id` are **optional** on `POST /v1/rerank`. The handler resolves each value from the incoming request (header names are case-insensitive). **Missing** or **whitespace-only** values are replaced with a **new UUID** (via `new_request_id()` in `app/core/logging.py`) before logging and before the upstream `httpx` call.

An **empty** `X-Request-Id` (after trimming) is treated like a missing header: a new UUID is generated. The **same** empty-as-missing rule applies to `X-Trace-Id` and `X-Session-Id` on this route.

| Header | Purpose |
|--------|---------|
| `X-Request-Id` | Correlation id (logged and forwarded) |
| `X-Trace-Id` | Trace id (logged and forwarded) |
| `X-Session-Id` | Session id (logged and forwarded) |

**JSON logs:** `JsonLogFormatter` writes `request_id`, `trace_id`, and `session_id` as **`"-"`** when the log record **omits** that field **or** the value is an **empty string** (see `_format_gateway` in `app/core/logging.py`). Empty `x-trace-id` / `x-session-id` values still yield **`"-"`** in JSON logs when they reach the formatter as blank strings (the same rule applies to `request_id`). Events that omit correlation kwargs show `"-"`; the `/v1/rerank` handler normally passes resolved non-empty UUIDs for all three.

### Request body (JSON)

Validated as `RerankRequest` in `app/models/schemas.py`:

| Field | Type | Notes |
|-------|------|--------|
| `model` | string | Required |
| `query` | string | Required; query text to rerank documents against |
| `documents` | list of strings | Required; candidate documents to score |
| `top_n` | integer | Optional; passed through to the backend, gateway does not enforce it |
| `conversation_id` | string | Optional; thread id (see `docs/conversation-id.md`). Omitted or blank → generated `conv_…` id. Stripped before upstream proxy. |
| `is_new_conversation` | any | Optional client field; **never** forwarded upstream; ignored for routing |

The gateway uses `classify()` only for **metrics and logs** (`request_class`), not for routing:

| `request_class` | Condition |
|-----------------|-----------|
| `SMALL_RERANK` | `len(documents) <= 4` and `total_chars <= 2048` |
| `MEDIUM_RERANK` | `len(documents) <= 32` and `total_chars <= 16384` |
| `LARGE_RERANK` | otherwise |

`total_chars` = `len(query)` + sum of `len(d)` over `documents`.

### Forwarded headers

The gateway forwards **almost all** incoming headers to the selected upstream, except hop-by-hop fields blocked in code: `host`, `content-length`, correlation headers replaced by canonical values, and `x-conversation-id` / `x-is-new-conversation` (gateway-injected; see `_BLOCKED_HEADERS` and `_CONVERSATION_KEYS_LOWER` in `app/api/rerank.py`).

### Successful response shape

On a completed upstream round-trip (including non-retryable HTTP errors from the backend), the gateway returns:

- **HTTP status**: same as upstream response (`upstream.status_code`)
- **Body**: upstream bytes, except for **2xx** JSON **object** responses where the gateway **merges** `conversation_id` and `is_new_conversation` into the JSON (see `docs/conversation-id.md`)
- **`Content-Type`**: upstream `content-type` header if present (merged JSON responses use `application/json`)

So clients may receive **2xx**, **4xx**, or **5xx** bodies **from the backend** without the gateway rewriting them, except where documented below (429/503 JSON from the gateway itself) or where conversation fields are merged into JSON objects on **2xx**.

### Gateway-generated error responses

| Status | When | Body (typical) |
|--------|------|----------------|
| **400** | Correlation IDs sent in JSON body (`request_id`, `trace_id`, etc.) | `{"error":"Correlation IDs must be provided via headers only: ..."}` |
| **400** | JSON body is not an object | `{"error":"JSON body must be an object"}` |
| **429** | Admission queue wait exceeded `ADMISSION_WAIT_TIMEOUT_MS` | `{"error":"Gateway busy, try again"}` |
| **503** | No backend to route to after exclusions / breaker | `{"detail":"No healthy backend available"}` (FastAPI) |
| **503** | All retry attempts failed with transport errors | `{"error":"Backends unavailable"}` |

See also `docs/status-codes.md` for 429/503 nuances (including upstream passthrough).

### Retries (client-visible behavior)

Configured by `RETRY_MAX_ATTEMPTS` (minimum 1). For each attempt the gateway may pick a different backend; failed backends are added to an internal `excluded` set for the rest of that client request.

- **Retry on HTTP status**: `502`, `503`, `504` when attempts remain (from `RetryConfig.retryable_statuses`).
- **Retry on transport errors**: `httpx.TimeoutException`, `httpx.HTTPError` when attempts remain.

**Circuit breaker bookkeeping**: `success = (upstream.status_code < 500)` for the selector. So **5xx** counts as a **failure** for the breaker even if the gateway returns the upstream body on the final attempt.

---

## Structured logging (JSON)

When JSON logging is enabled, `JsonLogFormatter` emits **one JSON object per line** for gateway events.

### Common envelope fields

| Field | Type | Meaning |
|-------|------|---------|
| `ts` | string (ISO-8601) | Timestamp in configured timezone (`LOG_TIMEZONE`, default `America/New_York`) |
| `level` | string | `INFO`, `WARN` (from `WARNING`), `ERROR`, … |
| `event` | string | Logical event name (see table below) |
| `service` | string | Always `gateway` for `log_gateway_event` |
| `env` | string | `GATEWAY_ENV` or `ENV`, default `dev` |
| `trace_id` | string or `"-"` | Resolved `X-Trace-Id` when set on the record; `"-"` if omitted or empty string on the record |
| `request_id` | string or `"-"` | Resolved `X-Request-Id` when set on the record; `"-"` if omitted or empty string on the record |
| `session_id` | string or `"-"` | Resolved `X-Session-Id` when set on the record; `"-"` if omitted or empty string on the record |
| `path` | string or `"-"` | Gateway route path (e.g. `/v1/rerank`) |
| `backend` | string or `"-"` | Upstream backend **name** when relevant |
| `conversation_id` | string or `"-"` | Thread id (`resolve_conversation_id`); `"-"` when omitted on the record (e.g. `gateway_process_started`) |
| `latency_ms` | number (optional) | **Upstream-only** duration: from start of `httpx.post` to completion (excludes admission wait and JSON parse before the post) |
| `queue_wait_ms` | number (optional) | Time spent waiting on the **admission semaphore** before handling |
| `status_code` | number (optional) | HTTP status when relevant (e.g. admission 429, upstream status on finish) |
| `error` | object (optional) | Structured error payload (`{"kind": ...}`) |
| `gateway_meta` | object (optional) | Event-specific metadata |

**Note:** `log_gateway_event` stores errors internally as `structured_error`, but the JSON output field is **`error`**.

### Event catalog

| `event` | Level | Typical `path` | `backend` | Key `gateway_meta` / other fields |
|---------|-------|----------------|-----------|-----------------------------------|
| `gateway_process_started` | INFO | — | — | `backends_count`, `admission_max_concurrent`, `admission_wait_timeout_ms` |
| `gateway_started` | INFO | `/v1/rerank` | — | Top-level: `conversation_id`. `gateway_meta`: `backends_count`, admission settings, `model`, `request_class`, `client_host` |
| `routing_pick` | INFO | `/v1/rerank` | chosen backend | Top-level: `conversation_id`. `gateway_meta`: `attempt`, `excluded`, **`decision_reason`**, **`backends`**, **`weights`** (see below) |
| `routing_no_backend` | WARN | `/v1/rerank` | — | Top-level: `conversation_id`. `gateway_meta`: `attempt`, `excluded`, same routing debug as `routing_pick` |
| `backend_retry` | WARN | `/v1/rerank` | failed backend | Top-level: `conversation_id`. `gateway_meta`: `attempt`; may include `latency_ms`, `status_code`, or `error` |
| `request_finished` | INFO | `/v1/rerank` | backend used | Top-level: `conversation_id`. `gateway_meta`: `model`, `request_class`, `attempt` |
| `request_failed` | WARN | `/v1/rerank` | — | Top-level: `conversation_id`; `error.kind` after retries exhausted |
| `request_rejected_invalid_body` | WARN | `/v1/rerank` | — | Top-level: `conversation_id` (from body before reject); `error.kind` |
| `admission_rejected` | WARN | `/v1/rerank` | — | `wait_timeout_ms`, `status_code` 429 |

### `routing_pick` / `routing_no_backend`: `gateway_meta` routing snapshot

`gateway_meta` includes:

- **`decision_reason`** (`routing_pick` only): `score`, `exploration`, `idle_rebalance`, or `none`
- **`attempt`**: retry attempt index (1-based)
- **`excluded`**: sorted list of backend names skipped for this client request
- **`backends`**: map of backend name → object:
  - `score`, `inflight`, `latency_ms`, `error_rate`, `requests`, `errors`
  - `circuit_open`, `circuit_half_open`, `half_open_inflight`, `half_open_successes`
  - `excluded`, `eligible`
- **`weights`**: routing weights used in score: `inflight`, `latency`, `error`

---

## Other endpoints (brief)

| Endpoint | Response |
|----------|----------|
| `GET /health` | `{"status":"ok"}` |
| `GET /ready` | `{"status":"ready"}` |
| `GET /metrics` | Prometheus text exposition |

---

## Operational notes

- **Log timezone**: controlled by `LOG_TIMEZONE` passed into `JsonLogFormatter` via `build_logging_config` in `app/main.py`.
- **Non-gateway logs** (e.g. some uvicorn internals) may use the formatter’s **legacy** JSON branch; gateway events always include `event`.

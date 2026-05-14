# Conversation ID (`conversation_id`)

This gateway assigns a **thread id** for each `POST /v1/rerank` call and surfaces it in logs, upstream headers, and client responses (same pattern as [layer-gateway-inference-v1](https://github.com/taixingbi/layer-gateway-inference-v1); see also their [conversation-id.md](https://github.com/taixingbi/layer-gateway-inference-v1/blob/main/docs/conversation-id.md)).

## Request body (`conversation_id`)

Send an optional string field **`conversation_id`** in the JSON body of `POST /v1/rerank`.

| Client sends | Effective `conversation_id` | `is_new_conversation` |
| --- | --- | --- |
| Field missing, or blank after trim | `conv_` + 32 hex characters | `true` |
| Non-blank string | That value (trimmed) | `false` |

The gateway strips **`conversation_id`** and any client **`is_new_conversation`** from the JSON **before** proxying to vLLM, so upstream does not receive those fields.

Implementation: [`app/core/conversation.py`](../app/core/conversation.py), wired in [`app/api/rerank.py`](../app/api/rerank.py).

## Upstream headers (to the model server)

On every proxied request the gateway sets:

- `x-conversation-id`: effective conversation id
- `x-is-new-conversation`: `true` or `false`

Incoming client `x-conversation-id` / `x-is-new-conversation` headers are **not** forwarded; gateway values win.

## Client response

For HTTP **2xx** responses whose body parses as a JSON **object**, the gateway **merges** into that object:

- `conversation_id`
- `is_new_conversation`

Response headers also include `x-conversation-id` and `x-is-new-conversation`.

If the upstream body is not JSON (or not an object), the body is passed through unchanged; response headers are still set when possible.

## Structured logs

`gateway_started` and `request_finished` include `conversation_id` and `is_new_conversation` in `gateway_meta`.

See also [`docs/request-response-and-logging.md`](request-response-and-logging.md) for the full HTTP contract.

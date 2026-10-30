# ADR 0009: An `Endpoint` and a `ModelProfile` say where a model is and what it accepts

**Status:** accepted · **Date:** 2026-10-30

## Context
The harness and the Memory Service both call models, and both reached them only through a
Bifrost gateway (`BIFROST_URL`, `BIFROST_VIRTUAL_KEY`). They now reach any provider: anything
OpenAI-compatible (OpenAI, OpenRouter, a LiteLLM proxy, Bifrost, Ollama, vLLM, llama.cpp, the
Anthropic and Gemini compatible endpoints), Vertex and Bedrock through a Google or AWS login.
Memory should call the model the harness was configured with, so the harness hands it one
value naming the route, the login and the model. Which request a model accepts (a reasoning
model takes `max_completion_tokens` and no `temperature`) was guessed from its name; it is
data LangChain already keeps per model (its model profile).

## Decision
- `ModelProfile` (frozen): `reasoning_output`, `temperature` (whether one is accepted),
  `max_output_tokens`, `tool_calling`. The names are LangChain's model-profile keys, mapped
  1:1, and the keys this type does not carry are ignored, so a chat model's `profile`
  validates as one. The defaults are a plain chat model.
- `Endpoint` (frozen, unknown fields refused): `base_url` (http or https), `gateway` (default
  `openai-compatible`), `auth` (default `bearer`), `model`, `api_key` (a `SecretStr`) and
  `profile`. `gateway` and `auth` are open strings: each consumer looks the name up in its own
  registry, which grows without a contracts release.
- `classify` maps bifrost-sdk's `BifrostOnlyError` (a Bifrost-only method on a client built
  for another gateway) to `VALIDATION`: a misconfiguration, not a failure to retry.

## Consequences
- A patch release (0.6.3): new types and one more name `classify` knows; nothing changes.
- `api_key` is masked by `repr` and `model_dump`: a sender puts `get_secret_value()` on the
  wire itself; a receiver's `Endpoint.model_validate` takes the plain string.
- bifrost-sdk does not import this package: the harness and the Memory Service map an
  `Endpoint` onto its arguments (`base_url`, `gateway`, `auth`, the two profile flags its
  requests are shaped from).

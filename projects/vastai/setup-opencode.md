# Study Log: Connecting OpenCode to a Protected Vast.ai LLM API

**Date:** 2026-06-07  
**Project:** Local Qwen Coding Agent on Vast.ai  
**Stack:** OpenCode, Vast.ai Tunnel, Caddy, llama.cpp, Qwen3.6 27B  
**Goal:** Connect OpenCode to my own protected OpenAI-compatible API endpoint.

---

## What I Set Up

After getting `llama-server` running on the Vast.ai instance, I exposed it through Caddy instead of exposing the model server directly.

The working path is:

```text
OpenCode
→ Vast.ai public tunnel
→ Caddy API key gate
→ llama-server
→ Qwen3.6 27B
```

The public API base URL is:

```text
https://progress-hook-url-index.trycloudflare.com/v1
```

The protected API key is stored on the Vast server at:

```text
/workspace/api-keys/current.key
```

---

## OpenCode Configuration

In OpenCode, I used an OpenAI-compatible provider.

```text
Provider:
OpenAI-compatible

Base URL:
https://progress-hook-url-index.trycloudflare.com/v1

Model:
qwen

API Key:
<contents of /workspace/api-keys/current.key>
```

Important detail:

```text
The base URL must end with /v1
```

Use:

```text
https://progress-hook-url-index.trycloudflare.com/v1
```

Not:

```text
https://progress-hook-url-index.trycloudflare.com
```

---

## Terminal Test Before OpenCode

Before using OpenCode, I tested the endpoint manually:

```bash
curl https://progress-hook-url-index.trycloudflare.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <API_KEY>" \
  -d '{
    "model": "qwen",
    "messages": [
      {
        "role": "user",
        "content": "Say ready."
      }
    ],
    "max_tokens": 100,
    "temperature": 0
  }'
```

The response confirmed:

```text
chat endpoint works
API key works
Caddy forwarding works
llama-server is reachable
```

---

## Token Usage Check

The API response included a `usage` object:

```json
{
  "completion_tokens": 100,
  "prompt_tokens": 13,
  "total_tokens": 113,
  "prompt_tokens_details": {
    "cached_tokens": 9
  }
}
```

Meaning:

```text
prompt_tokens = input tokens
completion_tokens = generated output tokens
total_tokens = input + output
cached_tokens = reused prompt cache tokens
```

The small test only showed 113 tokens because the prompt was tiny:

```text
Say ready.
```

For real OpenCode runs, the token count should be much larger because the agent sends repo context, instructions, tool outputs, and task history.

---

## Timing Check

The API also returned a `timings` object.

That showed:

```text
prompt_per_second = prompt processing speed
predicted_per_second = generation speed
draft_n = MTP draft tokens proposed
draft_n_accepted = MTP draft tokens accepted
```

This means I can check performance directly from the API response, not only from the server logs.

---

## Result

OpenCode can now use my Vast-hosted Qwen API like a normal OpenAI-compatible endpoint.

Current setup:

```text
Base URL:
https://progress-hook-url-index.trycloudflare.com/v1

Model:
qwen

Auth:
Bearer API key checked by Caddy
```

This gives me one protected API endpoint that I can reuse for coding agents and future local workflow agents.

# Study Log: Proper OpenCode Setup With Vast.ai Tunnels and Token Tracking

**Date:** 2026-06-08  
**Project:** Local Qwen Coding Agent on Vast.ai  
**Stack:** Vast.ai, llama.cpp, Caddy, Token Proxy, OpenCode  
**Goal:** Connect OpenCode to the correct Vast.ai tunnel and track whole-session token usage.

---

## Mini Summary

```text
Use 8787 tunnel for OpenCode if I want token tracking.
Use 18001 tunnel only for direct protected API calls.
Do not use old/raw llama-server tunnels for OpenCode.
```

Recommended OpenCode base URL:

```text
https://permits-configuring-passive-tap.trycloudflare.com/v1
```

Why:

```text
OpenCode
→ token logger :8787
→ Caddy auth :18001
→ llama-server :18000
```

---

## Tree Map

```text
OpenCode Setup
├── Tunnel Map
│   ├── 18001 = protected Caddy API
│   └── 8787 = token logger proxy
│
├── Recommended Path
│   └── OpenCode → 8787 tunnel → token logger → Caddy → llama-server
│
├── Config
│   ├── File: ~/.config/opencode/opencode.jsonc
│   ├── Provider ID: local-qwen
│   └── Base URL: https://permits-configuring-passive-tap.trycloudflare.com/v1
│
├── Auth
│   ├── Command: opencode auth login
│   ├── Choose: Other
│   └── Provider ID: local-qwen
│
├── Testing
│   ├── Test token proxy
│   ├── Test chat endpoint
│   └── Check session usage
│
└── Troubleshooting
    ├── Wrong tunnel
    ├── Missing /v1
    ├── Wrong provider key
    └── Auth not registered
```

---

## Table of Contents

- [1. Tunnel Map](#1-tunnel-map)
  - [1.1 Current Vast.ai Tunnels](#11-current-vastai-tunnels)
  - [1.2 Which Tunnel Matters](#12-which-tunnel-matters)
- [2. Recommended OpenCode Path](#2-recommended-opencode-path)
  - [2.1 Direct API Path](#21-direct-api-path)
  - [2.2 Token Logger Path](#22-token-logger-path)
- [3. Update OpenCode Config](#3-update-opencode-config)
  - [3.1 Open Global Config](#31-open-global-config)
  - [3.2 Paste Config](#32-paste-config)
  - [3.3 Save Config](#33-save-config)
- [4. Register OpenCode Auth](#4-register-opencode-auth)
  - [4.1 Run Auth Login](#41-run-auth-login)
  - [4.2 Provider ID](#42-provider-id)
  - [4.3 API Key](#43-api-key)
- [5. Start OpenCode](#5-start-opencode)
- [6. Check Token Usage](#6-check-token-usage)
  - [6.1 Whole Session Usage](#61-whole-session-usage)
  - [6.2 Per Request Usage](#62-per-request-usage)
  - [6.3 Timing / MTP Stats](#63-timing--mtp-stats)
- [7. Troubleshooting](#7-troubleshooting)
  - [7.1 OpenCode Cannot Connect](#71-opencode-cannot-connect)
  - [7.2 Token Totals Do Not Update](#72-token-totals-do-not-update)
  - [7.3 Wrong Config Shape](#73-wrong-config-shape)
- [8. Final Working Setup](#8-final-working-setup)

---

# 1. Tunnel Map

## 1.1 Current Vast.ai Tunnels

```text
Instance:
39989741

GPU:
NVIDIA GeForce RTX 3090
```

| Purpose | Local Target | Public Tunnel |
|---|---|---|
| Raw / older llama-server tunnel | `http://localhost:1111` | `https://len-gay-wins-told.trycloudflare.com` |
| Jupyter | `https://localhost:8080` | `https://energy-median-held-mods.trycloudflare.com` |
| Service / Syncthing | `http://localhost:8384` | `https://pairs-collector-calculators-increase.trycloudflare.com` |
| TensorBoard / service | `http://localhost:6006` | `https://julie-velvet-motors-buildings.trycloudflare.com` |
| Protected Caddy API | `http://localhost:18001` | `https://progress-hook-url-index.trycloudflare.com` |
| Token logger proxy | `http://localhost:8787` | `https://permits-configuring-passive-tap.trycloudflare.com` |

---

## 1.2 Which Tunnel Matters

For OpenCode with token tracking, use:

```text
https://permits-configuring-passive-tap.trycloudflare.com/v1
```

For direct API testing without session tracking, use:

```text
https://progress-hook-url-index.trycloudflare.com/v1
```

Do not use the raw/old tunnel for OpenCode:

```text
https://len-gay-wins-told.trycloudflare.com
```

---

# 2. Recommended OpenCode Path

## 2.1 Direct API Path

This works for normal model access:

```text
OpenCode
→ https://progress-hook-url-index.trycloudflare.com/v1
→ Caddy :18001
→ llama-server :18000
```

Use this only if I do **not** care about whole-session token tracking.

---

## 2.2 Token Logger Path

This is the recommended setup:

```text
OpenCode
→ https://permits-configuring-passive-tap.trycloudflare.com/v1
→ token logger :8787
→ Caddy :18001
→ llama-server :18000
```

Use this when I want:

```text
whole-session token totals
per-request usage logs
prompt/completion totals
cached token totals
```

---

# 3. Update OpenCode Config

## 3.1 Open Global Config

On the Mac:

```bash
mkdir -p ~/.config/opencode
nano ~/.config/opencode/opencode.jsonc
```

---

## 3.2 Paste Config

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "local-qwen": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local Qwen via Token Logger",
      "options": {
        "baseURL": "https://permits-configuring-passive-tap.trycloudflare.com/v1"
      },
      "models": {
        "qwen": {
          "name": "Qwen 27B Local"
        }
      }
    }
  }
}
```

Mini checks:

```text
provider = singular
local-qwen = provider ID
baseURL ends in /v1
8787 tunnel is used for token tracking
```

---

## 3.3 Save Config

In nano:

```text
CTRL + O
Enter
CTRL + X
```

Verify:

```bash
cat ~/.config/opencode/opencode.jsonc
```

---

# 4. Register OpenCode Auth

## 4.1 Run Auth Login

```bash
opencode auth login
```

---

## 4.2 Provider ID

Choose:

```text
Other
```

Provider ID:

```text
local-qwen
```

This must match:

```jsonc
"local-qwen": {
```

---

## 4.3 API Key

Paste the current API key from Vast:

```bash
cat /workspace/api-keys/current.key
```

If running from Mac, copy the key manually from the Vast server.

---

# 5. Start OpenCode

From any project:

```bash
opencode
```

Select:

```text
Local Qwen via Token Logger / Qwen 27B Local
```

Expected path:

```text
OpenCode
→ token logger tunnel
→ Caddy auth
→ Qwen llama-server
```

---

# 6. Check Token Usage

## 6.1 Whole Session Usage

After OpenCode sends requests:

```bash
curl https://permits-configuring-passive-tap.trycloudflare.com/session-usage | jq
```

Expected shape:

```json
{
  "requests": 3,
  "prompt_tokens": 12000,
  "completion_tokens": 900,
  "total_tokens": 12900,
  "cached_tokens": 5000
}
```

This is the main reason to use the `8787` tunnel.

---

## 6.2 Per Request Usage

```bash
curl -s https://permits-configuring-passive-tap.trycloudflare.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY_HERE" \
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
  }' | jq '.usage'
```

Meaning:

```text
prompt_tokens = input tokens
completion_tokens = generated tokens
total_tokens = input + output
cached_tokens = reused prompt cache
```

---

## 6.3 Timing / MTP Stats

```bash
curl -s https://permits-configuring-passive-tap.trycloudflare.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY_HERE" \
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
  }' | jq '.timings'
```

Useful fields:

```text
prompt_per_second = prompt processing speed
predicted_per_second = generation speed
draft_n = MTP draft tokens proposed
draft_n_accepted = MTP draft tokens accepted
```

---

# 7. Troubleshooting

## 7.1 OpenCode Cannot Connect

Test the token logger tunnel:

```bash
curl https://permits-configuring-passive-tap.trycloudflare.com/session-usage | jq
```

Test chat:

```bash
curl -s https://permits-configuring-passive-tap.trycloudflare.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY_HERE" \
  -d '{
    "model": "qwen",
    "messages": [{"role": "user", "content": "Say ready."}],
    "max_tokens": 100,
    "temperature": 0
  }' | jq '{usage, timings, finish_reason: .choices[0].finish_reason}'
```

If curl works but OpenCode fails, the issue is OpenCode config/auth.

---

## 7.2 Token Totals Do Not Update

OpenCode is probably using the direct Caddy tunnel:

```text
https://progress-hook-url-index.trycloudflare.com/v1
```

Switch it to the token logger tunnel:

```text
https://permits-configuring-passive-tap.trycloudflare.com/v1
```

---

## 7.3 Wrong Config Shape

Wrong:

```jsonc
{
  "providers": {}
}
```

Correct:

```jsonc
{
  "provider": {}
}
```

Also check:

```text
baseURL must end with /v1
provider ID must be local-qwen
auth must be registered with opencode auth login
```

---

# 8. Final Working Setup

Use this for OpenCode:

```text
Provider ID:
local-qwen

Display Name:
Local Qwen via Token Logger

Base URL:
https://permits-configuring-passive-tap.trycloudflare.com/v1

Model:
qwen

API key:
current key from /workspace/api-keys/current.key
```

Final path:

```text
OpenCode
→ Vast tunnel to token proxy :8787
→ token logger records usage
→ Caddy checks API key :18001
→ llama-server runs Qwen :18000
```

Main rule:

```text
Use the 8787 tunnel for OpenCode if I want whole-session token tracking.
Use the 18001 tunnel only for direct protected API calls.
```

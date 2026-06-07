# Vast.ai LLM API — Connectivity Incident & Resolution

## Problem

A llama.cpp inference server (Qwen3-27B) was running on a Vast.ai GPU instance and needed to be accessible externally via a secured API endpoint. Initial curl attempts from a local Mac timed out completely:

```
curl: (28) Failed to connect to <host> port 18001 after 75003 ms: Couldn't connect to server
```

## Investigation

### Step 1 — Confirmed Caddy was running

The instance used Caddy as a reverse proxy with bearer token auth, binding to port 18001 internally and forwarding to the llama.cpp server on port 18000. Caddy was running correctly inside the instance.

### Step 2 — Identified the port mapping issue

Vast.ai uses NAT — internal ports are not directly exposed. Only ports explicitly listed in the **Exposed Ports** field at instance creation time get an external mapping. Port 18001 was never exposed, so it had no external route.

The only externally mapped port was the one Vast auto-assigns for Jupyter, which was bound to Jupyter's own HTTPS server — not to Caddy.

Attempts to remap Caddy to the Jupyter port failed because Jupyter was already exclusively owning that port via HTTPS, causing connection resets on plain HTTP.

### Step 3 — Confirmed the backend was down

Even if connectivity had worked, the llama.cpp server on port 18000 had not been started yet:

```
curl: (7) Failed to connect to 127.0.0.1 port 18000 after 0 ms: Couldn't connect to server
```

The server was started manually and confirmed healthy:

```json
{"status":"ok"}
```

## Solution

### Cloudflare Tunnel (no instance rebuild required)

Since adding new exposed ports requires destroying and recreating the instance (losing all state), a Cloudflare Tunnel was used instead. This creates an outbound tunnel from the instance to Cloudflare's edge — no inbound port mapping needed.

**Install cloudflared on the instance:**

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
```

**Start the tunnel pointing at Caddy:**

```bash
cloudflared tunnel --url http://127.0.0.1:18001 &
```

This produces a public `trycloudflare.com` HTTPS URL that routes through Caddy's auth layer.

### Multi-key Auth in Caddy

The Caddyfile was updated to support multiple bearer tokens — one per user — so individual keys can be revoked without rotating the master key:

```caddy
{
    auto_https off
}
:18001 {
    @missingAuth {
        not header Authorization "Bearer {env.LOCAL_LLM_API_KEY}"
        not header Authorization "Bearer <friend-key>"
    }
    respond @missingAuth "Unauthorized" 401
    reverse_proxy 127.0.0.1:18000
}
```

The master key is loaded from a file via environment variable at Caddy startup:

```bash
export LOCAL_LLM_API_KEY=$(cat /workspace/api-keys/current.key)
caddy run --config /etc/caddy/Caddyfile &
```

### Key Rotation Script

A manual rotation script was created to generate a new master key, write it to disk, and restart Caddy with the new value. Saved at `/workspace/rotate-key.sh`:

```bash
#!/bin/bash
NEW_KEY=$(openssl rand -hex 32)
echo $NEW_KEY > /workspace/api-keys/current.key

# Kill existing Caddy — reload won't pick up new env vars
pkill caddy

# Restart with new key injected into environment
export LOCAL_LLM_API_KEY=$NEW_KEY
caddy run --config /etc/caddy/Caddyfile &

echo "Master key rotated: $NEW_KEY"
```

Make it executable:
```bash
chmod +x /workspace/rotate-key.sh
```

Run it anytime:
```bash
/workspace/rotate-key.sh
```

### Caddy Admin API Dead After Process Exit

`caddy reload` failed with:

```
Error: sending configuration to instance: performing request:
Post "http://localhost:2019/load": dial tcp [::1]:2019: connect: connection refused
```

This happens when the Caddy process has exited — the admin API on port 2019 only exists while Caddy is running. `caddy reload` is not a substitute for restart. Whenever Caddy is not running, always use:

```bash
pkill caddy  # ensure clean state
export LOCAL_LLM_API_KEY=$(cat /workspace/api-keys/current.key)
caddy run --config /etc/caddy/Caddyfile &
```

### Unexpected Key Rotation Mid-Session

During the heredoc config-writing incident, the rotate script at `/workspace/rotate-key.sh` fired as part of the shell confusion, silently replacing the master key in `/workspace/api-keys/current.key`. Caddy was still running with the old key in memory, so subsequent curl requests returned `Unauthorized` even with what appeared to be the correct key.

Diagnosis: compare the running key against the file:

```bash
echo $LOCAL_LLM_API_KEY        # what Caddy has in memory
cat /workspace/api-keys/current.key  # what's on disk
```

If they differ, the key rotated without Caddy restarting. Fix: restart Caddy to pick up the current key from disk:

```bash
pkill caddy
export LOCAL_LLM_API_KEY=$(cat /workspace/api-keys/current.key)
caddy run --config /etc/caddy/Caddyfile &
```

## Final Architecture

```
Mac / Friend's machine
        │
        │ HTTPS
        ▼
trycloudflare.com (Cloudflare edge)
        │
        │ outbound QUIC tunnel
        ▼
cloudflared (on Vast instance)
        │
        ▼
Caddy :18001 (bearer token auth, multi-key)
        │
        ▼
llama.cpp server :18000 (Qwen3-27B, GGUF)
```

## Caveats

- The `trycloudflare.com` URL is **ephemeral** — it changes on every cloudflared restart. For a stable URL, set up a named tunnel with a Cloudflare account.
- Caddy must be restarted (not just reloaded) when the master key rotates, since env vars are baked in at process start.
- Friend keys are hardcoded in the Caddyfile. To revoke one, remove its line and restart Caddy.

---

## Friend Onboarding

To give a friend access, send them their key and the following setup script. Replace `<FRIEND_KEY>` with the key you generated for them via `openssl rand -hex 32` and added to the Caddyfile.

### Setup Script

Save this as `setup-opencode.sh` and send it along with their key:

```bash
#!/bin/bash
TUNNEL_URL="https://concept-raise-virtually-accessible.trycloudflare.com"

# Write OpenCode provider config
cat > ~/.config/opencode/opencode.jsonc << EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "local-qwen": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local Qwen via Cloudflare",
      "options": {
        "baseURL": "${TUNNEL_URL}/v1"
      },
      "models": {
        "qwen": {
          "name": "Qwen 27B Local"
        }
      }
    }
  }
}
EOF

echo "Config written."
echo ""
echo "Now run: opencode auth login"
echo "  → Select 'Other'"
echo "  → Provider ID: local-qwen"
echo "  → API Key: <paste the key I gave you>"
echo ""
echo "Then restart OpenCode and select 'Local Qwen via Cloudflare' as your model."
```

### Instructions to Send

```
Hey! Here's how to connect to my Qwen 27B instance via OpenCode:

1. Run the setup script:
   bash setup-opencode.sh

2. Then run:
   opencode auth login
   → Choose "Other"
   → Provider ID: local-qwen
   → API Key: <their key>

3. Restart OpenCode — select "Local Qwen via Cloudflare" as the model.

Note: the URL may change if my instance restarts. I'll send a new script if that happens.
```

### Revoking Access

To revoke a friend's key, remove their `not header Authorization` line from `/etc/caddy/Caddyfile` on the Vast instance, then restart Caddy:

```bash
pkill caddy
export LOCAL_LLM_API_KEY=$(cat /workspace/api-keys/current.key)
caddy run --config /etc/caddy/Caddyfile &
```

---

## Part 2 — Connecting OpenCode

### Goal

Point OpenCode at the Cloudflare tunnel as a custom OpenAI-compatible provider.

### Issues Encountered

**Wrong config key** — the config used `"providers"` (plural) but OpenCode expects `"provider"` (singular). This caused a startup crash:

```
Error: 4 of 5 requests failed: Unexpected server error.
Affected startup requests: config.providers, provider.list, app.agents, config.get
```

**API key can't be hardcoded in config** — OpenCode requires credentials to be registered via `opencode auth login`, not inlined in the JSON. The key in `options.apiKey` is ignored by the running process.

**Heredoc pasted as literal text** — attempting to write the config via `cat > file << 'EOF'` in a terminal that was also running other processes caused the shell commands to be written into the file verbatim instead of executed. Always verify with `cat` after writing.

### Working Config

`~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "local-qwen": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local Qwen via Cloudflare",
      "options": {
        "baseURL": "https://<your-tunnel>.trycloudflare.com/v1"
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

### Registering the API Key

```bash
opencode auth login
```

Select **Other**, enter `local-qwen` as the provider ID (must match the key in config exactly), then paste the bearer token. OpenCode stores it as a credential linked to that provider ID.

### Restart OpenCode

After saving the config and registering the key, restart OpenCode. The provider appears in the model selector as "Local Qwen via Cloudflare".

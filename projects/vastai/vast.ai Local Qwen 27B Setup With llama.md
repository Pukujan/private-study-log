# Study Log: Vast.ai Local Qwen 27B Setup With llama.cpp

**Date:** 2026-06-07  
**Goal:** Create a Vast.ai GPU instance, open Jupyter, install `llama.cpp`, download/run Qwen3.6 27B, protect it with Caddy API key, and connect it to OpenCode.

---

## 0. Target Setup

```text
Vast.ai GPU instance
    ↓
Jupyter terminal
    ↓
llama.cpp server on 127.0.0.1:18000
    ↓
Caddy API-key proxy on 127.0.0.1:18001
    ↓
Cloudflare tunnel or SSH tunnel
    ↓
OpenCode
```

Model:

```text
unsloth/Qwen3.6-27B-MTP-GGUF:UD-Q4_K_XL
```

Recommended runtime:

```text
RTX 3090
40k context
OpenCode
TDD / phased coding
```

---

## 1. After Creating the Vast Instance

Open the instance from Vast:

```text
Instances → your running GPU → Jupyter
```

Open a terminal inside Jupyter:

```text
Launcher → Terminal
```

You should see something like:

```bash
root@C.xxxxx:/workspace$
```

Work inside:

```bash
/workspace
```

---

## 2. Prepare Environment

Run:

```bash
cd /workspace

export HF_HOME=/workspace/.hf_home
export LLAMA_CACHE=/workspace/.hf_home

apt update
apt install -y git cmake build-essential curl python3-pip gpg wget
```

Why:

```text
HF_HOME keeps model cache on /workspace.
llama.cpp will download the GGUF model into that cache.
```

---

## 3. Clone and Build llama.cpp

```bash
cd /workspace

git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp

cmake -B build \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_CUDA=ON

cmake --build build --config Release -j --target llama-server llama-cli
```

If using an older RTX 8000 / Turing card and you get CUDA architecture errors, rebuild with:

```bash
cd /workspace
rm -rf llama.cpp/build

cmake llama.cpp -B llama.cpp/build \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=75

cmake --build llama.cpp/build --config Release -j --target llama-server llama-cli
```

For RTX 3090 / 4090 / 5090, the normal build is usually fine.

---

## 4. Run Qwen3.6 27B With 40k Context

Start the model server:

```bash
cd /workspace/llama.cpp

export HF_HOME=/workspace/.hf_home
export LLAMA_CACHE=/workspace/.hf_home

./build/bin/llama-server \
  -hf unsloth/Qwen3.6-27B-MTP-GGUF:UD-Q4_K_XL \
  -ngl 99 \
  -c 40960 \
  -fa on \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 2 \
  --host 127.0.0.1 \
  --port 18000 \
  --jinja
```

Keep this terminal open.

Expected line:

```text
server is listening on http://127.0.0.1:18000
```

---

## 5. Test llama-server

Open another Jupyter terminal.

Run:

```bash
curl http://127.0.0.1:18000/health
```

Expected:

```json
{"status":"ok"}
```

Test chat:

```bash
curl http://127.0.0.1:18000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen",
    "messages": [
      {
        "role": "user",
        "content": "Say ready."
      }
    ],
    "max_tokens": 20,
    "temperature": 0
  }'
```

---

## 6. Create API Key

```bash
mkdir -p /workspace/api-keys

openssl rand -hex 32 > /workspace/api-keys/current.key
chmod 600 /workspace/api-keys/current.key

cat /workspace/api-keys/current.key
```

Save this key. It will be used in OpenCode.

---

## 7. Install Caddy

```bash
apt update
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl gpg

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list

apt update
apt install -y caddy
```

---

## 8. Configure Caddy API Key Proxy

Create the Caddyfile:

```bash
cat > /etc/caddy/Caddyfile <<'CADDY'
{
    auto_https off
}

:18001 {
    @missingAuth not header Authorization "Bearer {env.LOCAL_LLM_API_KEY}"

    respond @missingAuth "Unauthorized" 401

    reverse_proxy 127.0.0.1:18000
}
CADDY
```

Run Caddy:

```bash
export LOCAL_LLM_API_KEY="$(cat /workspace/api-keys/current.key)"

pkill caddy || true

caddy run --config /etc/caddy/Caddyfile
```

Keep this terminal open.

---

## 9. Test Caddy Locally

In another Vast terminal:

```bash
curl http://127.0.0.1:18001/health \
  -H "Authorization: Bearer $(cat /workspace/api-keys/current.key)"
```

Expected:

```json
{"status":"ok"}
```

Test unauthorized:

```bash
curl http://127.0.0.1:18001/health
```

Expected:

```text
Unauthorized
```

---

## 10. Expose API With Cloudflare Tunnel

Do **not** expose Jupyter token URLs to friends.

Install `cloudflared` on Vast if missing:

```bash
wget -O /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
apt install -y /tmp/cloudflared.deb
```

Run tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:18001
```

It will print something like:

```text
https://concept-raise-virtually-accessible.trycloudflare.com
```

Keep this terminal open.

Test from Mac:

```bash
curl https://YOUR-CLOUDFLARE-URL.trycloudflare.com/health \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Expected:

```json
{"status":"ok"}
```

---

## 11. OpenCode Config on Mac

On your Mac:

```bash
mkdir -p ~/.config/opencode
```

Create config:

```bash
cat > ~/.config/opencode/opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "local-qwen": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local Qwen via Cloudflare",
      "options": {
        "baseURL": "https://YOUR-CLOUDFLARE-URL.trycloudflare.com/v1",
        "apiKey": "YOUR_API_KEY"
      },
      "models": {
        "qwen": {
          "name": "Qwen 27B Local"
        }
      }
    }
  }
}
JSON
```

Replace:

```text
YOUR-CLOUDFLARE-URL.trycloudflare.com
YOUR_API_KEY
```

Example:

```json
"baseURL": "https://concept-raise-virtually-accessible.trycloudflare.com/v1",
"apiKey": "18a9c0a6dd09aa91ebdbdb7129d974033556c6f1bf8a4fa86c3e0f31f9c15ff5"
```

---

## 12. Run OpenCode

Go to project folder:

```bash
cd ~/Documents/coding/legal-prmpt-eng
opencode
```

Inside OpenCode:

```text
/models
```

Select:

```text
Local Qwen via Cloudflare → Qwen 27B Local
```

---

## 13. API Key Rotation Script

Create:

```bash
cat > /workspace/rotate_and_restart_caddy_key.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="/workspace/api-keys/current.key"
CADDYFILE="/etc/caddy/Caddyfile"

mkdir -p /workspace/api-keys

openssl rand -hex 32 > "$KEY_FILE"
chmod 600 "$KEY_FILE"

export LOCAL_LLM_API_KEY="$(cat "$KEY_FILE")"

pkill caddy || true
sleep 1

nohup caddy run --config "$CADDYFILE" > /workspace/caddy.log 2>&1 &

echo "New API key:"
cat "$KEY_FILE"

echo
echo "Caddy restarted with new key."
SH

chmod +x /workspace/rotate_and_restart_caddy_key.sh
```

Run:

```bash
/workspace/rotate_and_restart_caddy_key.sh
```

After rotating, update OpenCode config with the new key:

```bash
cat /workspace/api-keys/current.key
```

---

## 14. Useful Health Checks

Check model:

```bash
curl http://127.0.0.1:18000/health
```

Check Caddy:

```bash
curl http://127.0.0.1:18001/health \
  -H "Authorization: Bearer $(cat /workspace/api-keys/current.key)"
```

Check Cloudflare tunnel from Mac:

```bash
curl https://YOUR-CLOUDFLARE-URL.trycloudflare.com/health \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Check disk:

```bash
du -h --max-depth=2 /workspace | sort -h
```

Check GPU:

```bash
nvidia-smi
```

---

## 15. Recommended Daily Startup Order

After restarting Vast:

```text
1. Open Jupyter terminal
2. Start llama-server on 127.0.0.1:18000
3. Start Caddy on 127.0.0.1:18001
4. Start Cloudflare tunnel to 18001
5. Update OpenCode config if tunnel URL changed
6. Start OpenCode
```

---

## 16. Common Mistakes

### Mistake: Using Jupyter URL in OpenCode

Wrong:

```text
https://185.41.130.73:31120/?token=...
```

Do not send this to friends.

Use Cloudflare tunnel instead:

```text
https://something.trycloudflare.com/v1
```

---

### Mistake: Using `/chat/completions` in baseURL

Wrong:

```text
https://something.trycloudflare.com/v1/chat/completions
```

Correct:

```text
https://something.trycloudflare.com/v1
```

OpenCode adds `/chat/completions` itself.

---

### Mistake: Typing `Bearer` into OpenCode key

Wrong:

```text
Bearer abc123
```

Correct:

```text
abc123
```

---

### Mistake: Running Caddy on Vast portal ports

Avoid using:

```text
8080
1111
6006
8384
```

Those are used by Vast services.

Use:

```text
18001
```

and expose it through Cloudflare tunnel.

---

## Final Working Config

OpenCode:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "local-qwen": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local Qwen via Cloudflare",
      "options": {
        "baseURL": "https://YOUR-CLOUDFLARE-URL.trycloudflare.com/v1",
        "apiKey": "YOUR_API_KEY"
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

Runtime:

```text
llama-server: 127.0.0.1:18000
Caddy:        127.0.0.1:18001
Cloudflare:   https://something.trycloudflare.com
OpenCode:     https://something.trycloudflare.com/v1
```

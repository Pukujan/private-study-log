curl -s https://progress-hook-url-index.trycloudflare.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 67f11ac71387ef2c087b1ee4f1ea16f5334b87214abf344c0a68b694e710172f" \
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
  }' | jq '{usage, timings, finish_reason: .choices[0].finish_reason}'


 Here's the context compression config from C:/Users/pujan/AppData/Local/hermes/config.yaml:

    yaml
    compression:
      enabled: true
      threshold: 0.84
      target_ratio: 0.05
      protect_last_n: 15
      hygiene_hard_message_limit: 400
      protect_first_n: 3
      abort_on_summary_failure: false
      codex_gpt55_autoraise: true
      in_place: true

    context:
      engine: compressor

    auxiliary:
      compression:
        provider: openrouter
        model: openai/gpt-4o-mini
        base_url: ''
        api_key: ''
        timeout: 120
        extra_body: {}

    memory:
      memory_char_limit: 2200
      user_char_limit: 1375


    Key knobs:
    - threshold: 0.84 — triggers compression when context hits 84%
    - target_ratio: 0.05 — compresses down to 5% of original size
    - protect_last_n: 15 — keeps the last 15 messages uncompressed
    - protect_first_n: 3 — keeps the first 3 messages uncompressed
    - hygiene_hard_message_limit: 400 — hygiene pass after 400 messages
    - in_place: true — compresses in the existing context rather than creating a new one
    - compression model: openai/gpt-4o-mini via openrouter
    - context engine: compressor (not window-based)
    - memory char limits: 2200 for memory, 1375 for user profile

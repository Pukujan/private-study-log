 That's the issue. Classic Windows popup hell.

    The culprit is inline_shell: false (line 424 in the Hades profile config, and 425 in default). When false, Hermes spawns a fresh cmd.exe /c process for every terminal command — each one opens a new console window, flashes, and closes. Ugly.

    The gateway process itself (pythonw.exe) is windowless — it's the terminal backend spawning all those cmd windows per-command.

    Fix: set inline_shell: true in the Hades profile config.

    This makes Hermes run terminal commands inline in the gateway's own Python process instead of spawning new cmd.exe windows. No more popups, no more flash.

    yaml
    in profiles/hades/config.yaml
    skills:
      inline_shell: true
      inline_shell_timeout: 10


    Want me to patch it?

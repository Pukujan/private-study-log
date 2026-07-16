from __future__ import annotations

from cortex_core.plugin import post_llm_call, pre_llm_call, register


class DummyContext:
    def __init__(self) -> None:
        self.hooks = {}
        self.skills = {}
        self.metadata = {}

    def register_hook(self, name, value):
        self.hooks[name] = value

    def register_skill(self, name, value):
        self.skills[name] = value


def test_register_adds_hooks_and_skills() -> None:
    ctx = DummyContext()
    result = register(ctx)

    assert "pre_llm_call" in ctx.hooks
    assert "post_llm_call" in ctx.hooks
    assert ctx.skills["cortex-skill"].endswith("SKILL.md")
    assert ctx.metadata["cortex_workspace"]
    assert result["hooks"] == ["pre_llm_call", "post_llm_call"]


def test_hooks_return_useful_payloads(tmp_path) -> None:
    ctx = DummyContext()
    assert "workspace" in pre_llm_call(ctx)
    payload = post_llm_call(ctx, task="demo", result="ok", write_closeout=False)
    assert payload["logged"] is False
    assert "memory" in payload

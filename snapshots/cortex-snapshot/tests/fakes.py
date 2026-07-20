"""Shared test doubles for the Eval Flywheel suite (GLM-5.2 TDD)."""


class FakeModel:
    """A scripted, dict-backed model: callable str -> str, no network. `responses` maps a
    prompt to its canned completion (missing prompt -> "")."""

    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.responses.get(prompt, "")

from __future__ import annotations

from dataclasses import dataclass


class LLMProvider:
    name = "base"

    def summarize(self, prompt: str) -> str:
        raise NotImplementedError


class NoOpLLMProvider(LLMProvider):
    name = "noop"

    def summarize(self, prompt: str) -> str:
        return ""


@dataclass
class OpenAIProvider(LLMProvider):
    api_key: str | None = None
    model: str = "gpt-4.1-mini"
    name = "openai"

    def summarize(self, prompt: str) -> str:
        raise RuntimeError(
            "OpenAIProvider is a placeholder. Core scoring does not require LLM use; "
            "wire the official OpenAI client only after configuring credentials."
        )


@dataclass
class LocalLLMProvider(LLMProvider):
    base_url: str | None = None
    name = "local"

    def summarize(self, prompt: str) -> str:
        raise RuntimeError("LocalLLMProvider is a placeholder until a local endpoint is configured.")

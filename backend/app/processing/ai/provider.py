"""LLM provider abstraction. Providers return raw text; validation happens in the caller."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """The provider could not produce a response (auth, network, quota…)."""


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    provider: str
    model: str


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, prompt: str) -> Completion: ...

    async def close(self) -> None: ...

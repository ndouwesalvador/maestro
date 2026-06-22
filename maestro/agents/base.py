"""Common agent interface shared by every backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Response:
    text: str
    usage: Usage
    model: str = ""


class Agent(ABC):
    """A model wearing one of two hats: 'supervisor' or 'executor'."""

    role: str = ""
    name: str = ""
    model: str = ""

    @abstractmethod
    def chat(self, system: str, user: str) -> Response:
        """Single-turn completion. Returns text plus token usage."""
        raise NotImplementedError


def estimate_tokens(text: str) -> int:
    """Rough fallback estimate (~4 chars/token) for backends that don't
    report usage. Real backends should return exact counts."""
    return max(1, len(text) // 4)

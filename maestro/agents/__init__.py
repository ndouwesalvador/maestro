"""Agent backends. Each backend is a thin adapter exposing the same `chat`
interface, so any model can play the Supervisor or the Executor role.
"""

from .base import Agent, Response, Usage, estimate_tokens

__all__ = ["Agent", "Response", "Usage", "estimate_tokens"]

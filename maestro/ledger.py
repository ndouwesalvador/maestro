"""Token ledger — the honest accounting of what Maestro actually saved.

The headline question Maestro answers is: *how much of the paid-token cost did
we shift onto the free local model?* We compute it transparently:

    actual_cost   = supervisor_tokens @ supervisor_price
                  + executor_tokens   @ executor_price   (0 if local)

    baseline_cost = (all tokens)      @ supervisor_price
                    # i.e. what it would have cost if the frontier model
                    # had done every single token of the job itself.

    savings       = 1 - actual_cost / baseline_cost
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Price:
    """USD per 1,000,000 tokens."""

    input_per_mtok: float
    output_per_mtok: float


@dataclass
class Entry:
    agent: str
    role: str            # "supervisor" | "executor"
    input_tokens: int
    output_tokens: int
    label: str = ""      # e.g. "plan", "exec:s1", "intervene:s1"


class Ledger:
    def __init__(self, supervisor_price: Price, executor_price: Price) -> None:
        self.supervisor_price = supervisor_price
        self.executor_price = executor_price
        self.entries: List[Entry] = []

    # -- recording --------------------------------------------------------- #
    def record(self, agent: str, role: str, usage, label: str = "") -> None:
        self.entries.append(
            Entry(
                agent=agent,
                role=role,
                input_tokens=int(getattr(usage, "input_tokens", 0)),
                output_tokens=int(getattr(usage, "output_tokens", 0)),
                label=label,
            )
        )

    # -- aggregates -------------------------------------------------------- #
    def _tokens(self, role: str | None = None):
        ins = sum(e.input_tokens for e in self.entries if role in (None, e.role))
        outs = sum(e.output_tokens for e in self.entries if role in (None, e.role))
        return ins, outs

    @staticmethod
    def _cost(inp: int, out: int, price: Price) -> float:
        return inp / 1e6 * price.input_per_mtok + out / 1e6 * price.output_per_mtok

    def actual_cost(self) -> float:
        s_in, s_out = self._tokens("supervisor")
        e_in, e_out = self._tokens("executor")
        return self._cost(s_in, s_out, self.supervisor_price) + self._cost(
            e_in, e_out, self.executor_price
        )

    def baseline_cost(self) -> float:
        """What a frontier-only agent would have cost (every token at frontier price)."""
        a_in, a_out = self._tokens(None)
        return self._cost(a_in, a_out, self.supervisor_price)

    def savings_pct(self) -> float:
        base = self.baseline_cost()
        if base <= 0:
            return 0.0
        return max(0.0, 1.0 - self.actual_cost() / base)

    # -- presentation ------------------------------------------------------ #
    def summary(self) -> str:
        s_in, s_out = self._tokens("supervisor")
        e_in, e_out = self._tokens("executor")
        actual = self.actual_cost()
        base = self.baseline_cost()
        saved = self.savings_pct()

        lines = [
            "+--------------------------------------------------------------+",
            "|  TOKEN LEDGER                                                |",
            "+--------------------------------------------------------------+",
            f"  Supervisor (paid)   in={s_in:>8}  out={s_out:>8}",
            f"  Executor   (local)  in={e_in:>8}  out={e_out:>8}",
            "  " + "-" * 56,
            f"  Actual paid cost            ${actual:0.5f}",
            f"  Frontier-only baseline      ${base:0.5f}",
            f"  >> PAID-TOKEN SAVINGS:      {saved * 100:0.1f}%",
            "+--------------------------------------------------------------+",
            "  (Prices are illustrative - set them in maestro/config.py.)",
        ]
        return "\n".join(lines)

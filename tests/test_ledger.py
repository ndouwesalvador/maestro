from maestro.agents.base import Usage
from maestro.ledger import Ledger, Price


def test_savings_shifted_to_free_executor():
    ledger = Ledger(supervisor_price=Price(10, 10), executor_price=Price(0, 0))
    ledger.record("sup", "supervisor", Usage(100, 100))
    ledger.record("ex", "executor", Usage(900, 900))

    # Frontier-only baseline prices all 2000 tokens at $10/Mtok = $0.02.
    # Actual pays only for the 200 supervisor tokens = $0.002.
    assert abs(ledger.baseline_cost() - 0.02) < 1e-9
    assert abs(ledger.actual_cost() - 0.002) < 1e-9
    assert abs(ledger.savings_pct() - 0.9) < 1e-9


def test_no_tokens_means_no_savings():
    ledger = Ledger(Price(5, 25), Price(0, 0))
    assert ledger.savings_pct() == 0.0

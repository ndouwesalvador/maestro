from math_utils import factorial, is_prime


def test_factorial():
    assert factorial(0) == 1
    assert factorial(5) == 120


def test_is_prime():
    assert is_prime(7)
    assert not is_prime(8)
    assert not is_prime(1)

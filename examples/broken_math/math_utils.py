def factorial(n):
    result = 1
    for i in range(1, n):  # BUG: range stops one short; should be range(1, n + 1)
        result *= i
    return result


def is_prime(n):
    if n < 2:
        return False
    for d in range(2, int(n ** 0.5) + 1):
        if n % d == 0:
            return False
    return True

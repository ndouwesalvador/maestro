from maestro.focus import mentioned_files, savings

REPO = ["src/app.ts", "src/util.ts", "tests/test_math.py", "math_utils.py", "README.md"]


def test_finds_the_file_named_in_a_python_traceback():
    out = (
        'Traceback (most recent call last):\n'
        '  File "D:\\\\proj\\\\tests\\\\test_math.py", line 4, in test_factorial\n'
        '    assert factorial(5) == 120\n'
        '  File "D:\\\\proj\\\\math_utils.py", line 7, in factorial\n'
        'AssertionError\n'
    )
    assert set(mentioned_files(out, REPO)) == {"tests/test_math.py", "math_utils.py"}


def test_finds_the_file_named_in_a_tsc_diagnostic():
    out = "src/app.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'."
    assert mentioned_files(out, REPO) == ["src/app.ts"]


def test_ranks_the_most_referenced_file_first():
    out = "src/util.ts:1:1 error\nsrc/util.ts:9:2 error\nsrc/app.ts:3:1 error\n"
    assert mentioned_files(out, REPO)[0] == "src/util.ts"


def test_ignores_paths_that_are_not_in_the_repo():
    out = 'File "/usr/lib/python3/site-packages/pytest/__init__.py", line 1\n'
    assert mentioned_files(out, REPO) == []


def test_returns_nothing_for_output_without_paths():
    assert mentioned_files("all tests failed", REPO) == []
    assert mentioned_files("", REPO) == []


def test_savings_reports_the_reduction():
    s = savings(40000, 4000)
    assert s["full_tokens"] == 10000
    assert s["focused_tokens"] == 1000
    assert s["pct"] == 90.0

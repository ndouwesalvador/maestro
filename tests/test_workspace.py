from maestro.protocol import Edit
from maestro.workspace import Workspace


def test_lists_files_even_under_ignored_ancestor(tmp_path):
    # Workspace placed under a directory named like an ignore entry (.maestro).
    root = tmp_path / ".maestro" / "project"
    root.mkdir(parents=True)
    (root / "math_utils.py").write_text("x = 1\n", encoding="utf-8")

    ws = Workspace(root)
    assert ws.files() == ["math_utils.py"]
    assert "math_utils.py" in ws.context()


def test_basename_fallback_when_path_is_prefixed(tmp_path):
    (tmp_path / "math_utils.py").write_text("value = 1\n", encoding="utf-8")
    ws = Workspace(tmp_path)

    # A model that prefixes the path should still hit the right file.
    edit = Edit(path="examples/broken_math/math_utils.py", search="value = 1", replace="value = 2")
    outcome = ws.apply_edits([edit])

    assert outcome.ok
    assert outcome.files_changed == ["math_utils.py"]
    assert (tmp_path / "math_utils.py").read_text(encoding="utf-8") == "value = 2\n"

"""tests/test_circular_deps.py — 循环导入检测器自身的回归测试。

把「工程纪律」变成可执行断言：

    * 模块级 import 形成的环（A -> B -> A）必须报出来；
    * 函数体内 / `if TYPE_CHECKING:` 内的 import 是**延迟导入**——正是本项目
      用来断环的手段——默认不算环；`--include-lazy` 时才纳入审计。

顺带覆盖 CLI 退出码（0 无环 / 1 有环 / 2 用法错误），可直接挂进 CI。
"""
from __future__ import annotations

from pathlib import Path

import check_circular_deps as detector


def _write(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")


def _make_pkg(root: Path, name: str = "pkg") -> Path:
    pkg = root / name
    pkg.mkdir()
    _write(pkg / "__init__.py", "")
    return pkg


def _cycles(root: Path, **kwargs) -> tuple[list, int]:
    graph, skipped = detector.build_graph(root, set(), **kwargs)
    return detector.find_cycles(graph), skipped


# --------------------------------------------------------------------------- #
# 用例 1：模块级环必须被检出（绝对 + 相对两种写法）
# --------------------------------------------------------------------------- #
def test_module_level_absolute_cycle_is_detected(tmp_path) -> None:
    # Arrange: a -> b -> a，都是模块级 import
    pkg = _make_pkg(tmp_path)
    _write(pkg / "a.py", "from pkg import b\n")
    _write(pkg / "b.py", "from pkg import a\n")

    # Act
    cycles, _ = _cycles(tmp_path)

    # Assert
    assert len(cycles) == 1
    assert set(cycles[0]) == {"pkg.a", "pkg.b"}


def test_relative_three_node_cycle_is_detected(tmp_path) -> None:
    # Arrange: x -> y -> z -> x，全用相对导入
    app = _make_pkg(tmp_path, "app")
    _write(app / "x.py", "from . import y\n")
    _write(app / "y.py", "from . import z\n")
    _write(app / "z.py", "from . import x\n")

    # Act
    cycles, _ = _cycles(tmp_path)

    # Assert
    assert len(cycles) == 1
    assert set(cycles[0]) == {"app.x", "app.y", "app.z"}


# --------------------------------------------------------------------------- #
# 用例 2：延迟导入默认不算环（这正是本项目断环的手段）
# --------------------------------------------------------------------------- #
def test_function_scoped_lazy_import_is_ignored_by_default(tmp_path) -> None:
    # Arrange: a --(模块级)--> b，b --(函数体内)--> a，不是运行期环
    pkg = _make_pkg(tmp_path)
    _write(pkg / "a.py", "from pkg import b\n")
    _write(pkg / "b.py", "def use():\n    from pkg import a\n    return a\n")

    # Act: 默认忽略延迟导入
    cycles, skipped = _cycles(tmp_path)

    # Assert
    assert cycles == []
    assert skipped == 1

    # Act: 显式审计时才纳入
    cycles_with_lazy, _ = _cycles(tmp_path, include_lazy=True)
    assert len(cycles_with_lazy) == 1


def test_type_checking_guarded_import_is_ignored(tmp_path) -> None:
    # Arrange: a 只在 TYPE_CHECKING 下引用 b；b 模块级引用 a
    pkg = _make_pkg(tmp_path)
    _write(
        pkg / "a.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from pkg import b\n",
    )
    _write(pkg / "b.py", "from pkg import a\n")

    # Act
    cycles, skipped = _cycles(tmp_path)

    # Assert
    assert cycles == []
    assert skipped == 1


# --------------------------------------------------------------------------- #
# 用例 3：干净项目无环 + CLI 退出码
# --------------------------------------------------------------------------- #
def test_clean_project_has_no_cycle_and_main_exits_zero(tmp_path) -> None:
    # Arrange
    pkg = _make_pkg(tmp_path)
    _write(pkg / "a.py", "from pkg import b\n")
    _write(pkg / "b.py", "import os\n")

    # Act
    cycles, _ = _cycles(tmp_path)

    # Assert
    assert cycles == []
    assert detector.main([str(tmp_path)]) == 0


def test_main_exits_one_when_cycle_found(tmp_path) -> None:
    # Arrange
    pkg = _make_pkg(tmp_path)
    _write(pkg / "a.py", "from pkg import b\n")
    _write(pkg / "b.py", "from pkg import a\n")

    # Act / Assert
    assert detector.main([str(tmp_path)]) == 1


def test_main_returns_usage_error_for_missing_root(tmp_path) -> None:
    # Arrange
    missing = tmp_path / "does-not-exist"

    # Act / Assert
    assert detector.main([str(missing)]) == 2


# --------------------------------------------------------------------------- #
# 用例 4：自身即 CI 门禁 —— 本工作区必须零运行时环
# --------------------------------------------------------------------------- #
def test_this_workspace_has_no_runtime_cycles() -> None:
    # Arrange
    root = Path(detector.__file__).resolve().parent

    # Act
    graph, _ = detector.build_graph(root, detector.DEFAULT_EXCLUDES)
    cycles = detector.find_cycles(graph)

    # Assert
    assert cycles == [], f"检出循环导入：{cycles}"

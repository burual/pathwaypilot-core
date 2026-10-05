#!/usr/bin/env python3
"""check_circular_deps.py

Static circular-import detector for a Python project.

Walks the project, parses every .py file with `ast`, builds a module-level
import graph (resolving BOTH absolute and relative imports), and reports any
cycle such as  A -> B -> A .

Scope: only **runtime** imports are considered. Two kinds of import are
ignored by default because neither can produce an import-time cycle:

    * imports inside a function/method body  -> lazy import, the canonical
      way to break a cycle (this project relies on it);
    * imports under `if TYPE_CHECKING:`      -> never executed at runtime.

Pass --include-lazy to have them counted anyway (useful when auditing).

Usage:
    python check_circular_deps.py [PROJECT_ROOT] [--exclude dir1 dir2] [--include-lazy]

Exit code:
    0  -> no circular dependency
    1  -> at least one cycle found (CI friendly)
    2  -> usage / runtime error
"""
from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path
from typing import Dict, List, Set

# Directories skipped by default (third-party, generated, VCS, caches).
DEFAULT_EXCLUDES = {
    ".git", ".hg", ".svn",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", "venv", "env", ".venv", ".workbuddy",
    "dist", "build", ".tox",
}


def find_py_files(root: Path, excludes: Set[str]) -> List[Path]:
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded / hidden dirs in place so os.walk does not descend.
        dirnames[:] = [d for d in dirnames if d not in excludes and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(Path(dirpath) / fn)
    return files


def path_to_module(root: Path, path: Path) -> str:
    """Convert a file path to its dotted module name relative to root."""
    rel = path.relative_to(root)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve_target(root: Path, module_name: str) -> str | None:
    """Map a dotted name to the *actual* existing module node.

    Tries the longest path first (submodule), then walks up to the package,
    so `a.b.c` resolves to `a.b.c` if it exists, else `a.b`, etc.
    Returns None for anything outside the project (stdlib / third-party).
    """
    parts = module_name.split(".")
    for i in range(len(parts), 0, -1):
        cand = parts[:i]
        pkg_init = root.joinpath(*cand, "__init__.py")
        mod_file = root.joinpath(*cand).with_suffix(".py")
        if pkg_init.exists() or mod_file.exists():
            return ".".join(cand)
    return None


def _is_type_checking_guard(test: ast.expr) -> bool:
    """识别 `if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:`（含 not 形式）。"""
    node = test.operand if isinstance(test, ast.UnaryOp) else test
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _collect_import_nodes(
    node: ast.AST, lazy: bool, out: List[tuple]
) -> None:
    """收集 Import/ImportFrom 节点，并标注它是不是「延迟导入」。

    lazy=True 的两种情况：
      1. 位于函数/方法体内 —— 调用时才执行，**这正是用来断环的手段**；
      2. 位于 `if TYPE_CHECKING:` 守卫内 —— 仅类型检查期存在，运行期没有该语句。
    两者都不可能造成运行期循环导入，默认不计入。
    """
    for child in ast.iter_child_nodes(node):
        child_lazy = lazy
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) or (isinstance(child, ast.If) and _is_type_checking_guard(child.test)):
            child_lazy = True
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            out.append((child, child_lazy))
        _collect_import_nodes(child, child_lazy, out)


def _resolve_import_node(
    node: ast.AST, root: Path, src_pkg_parts: List[str]
) -> Set[str]:
    """把一个 Import / ImportFrom 节点解析为项目内模块集合。"""
    targets: Set[str] = set()

    if isinstance(node, ast.Import):
        for alias in node.names:
            tgt = resolve_target(root, alias.name)
            if tgt:
                targets.add(tgt)
        return targets

    # ast.ImportFrom
    level = node.level or 0
    if level > 0:
        # Relative: `.` = containing package, `..` = its parent, etc.
        if level - 1 > len(src_pkg_parts):
            return targets
        base_str = ".".join(src_pkg_parts[: len(src_pkg_parts) - (level - 1)])
    else:
        # Absolute: base is empty; module is the full dotted path.
        base_str = ""

    module = node.module or ""
    segs = []
    if base_str:
        segs.append(base_str)
    if module:
        segs.append(module)
    prefix = ".".join(segs)

    for alias in node.names:
        # Prefer the submodule (pkg.b), fall back to the package (pkg).
        cand = f"{prefix}.{alias.name}" if prefix else alias.name
        tgt = resolve_target(root, cand)
        if tgt is None and prefix:
            tgt = resolve_target(root, prefix)
        if tgt:
            targets.add(tgt)
    return targets


def parse_imports(
    root: Path, path: Path, src_module: str, is_pkg: bool, *, include_lazy: bool = False
) -> tuple:
    """Return (intra-project targets, number of skipped lazy imports)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"[warn] skipping {path}: {exc}", file=sys.stderr)
        return set(), 0

    # The package that *contains* this module (for relative imports).
    if is_pkg:
        src_pkg = src_module
    elif "." in src_module:
        src_pkg = src_module.rsplit(".", 1)[0]
    else:
        src_pkg = ""
    src_pkg_parts = src_pkg.split(".") if src_pkg else []

    nodes: List[tuple] = []
    _collect_import_nodes(tree, False, nodes)

    targets: Set[str] = set()
    skipped = 0
    for node, is_lazy in nodes:
        if is_lazy and not include_lazy:
            skipped += 1
            continue
        targets |= _resolve_import_node(node, root, src_pkg_parts)

    return targets, skipped


def build_graph(
    root: Path, excludes: Set[str], *, include_lazy: bool = False
) -> tuple:
    """Build the runtime import graph. Returns (graph, skipped_lazy_import_count)."""
    files = find_py_files(root, excludes)
    graph: Dict[str, Set[str]] = {}
    for f in files:
        m = path_to_module(root, f)
        if m:
            graph.setdefault(m, set())

    skipped_total = 0
    for f in files:
        m = path_to_module(root, f)
        if not m:
            continue
        is_pkg = f.name == "__init__.py"
        targets, skipped = parse_imports(
            root, f, m, is_pkg, include_lazy=include_lazy
        )
        skipped_total += skipped
        for tgt in targets:
            if tgt in graph and tgt != m:
                graph[m].add(tgt)
    return graph, skipped_total


def find_cycles(graph: Dict[str, Set[str]]) -> List[List[str]]:
    """DFS-based cycle detection. Returns de-duplicated canonical cycles."""
    sys.setrecursionlimit(max(10000, len(graph) + 100))
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = dict.fromkeys(graph, WHITE)
    found: List[List[str]] = []
    seen: Set[frozenset] = set()
    stack: List[str] = []

    def dfs(u: str) -> None:
        color[u] = GRAY
        stack.append(u)
        for v in sorted(graph.get(u, ())):
            if color.get(v, WHITE) == GRAY:
                idx = stack.index(v)
                cyc = stack[idx:] + [v]
                key = frozenset((cyc[i], cyc[i + 1]) for i in range(len(cyc) - 1))
                if key not in seen:
                    seen.add(key)
                    found.append(cyc)
            elif color.get(v, WHITE) == WHITE:
                dfs(v)
        stack.pop()
        color[u] = BLACK

    for n in graph:
        if color[n] == WHITE:
            dfs(n)
    return found


def _render(cycle: List[str]) -> str:
    # Rotate so the cycle starts at its alphabetically smallest node.
    start = min(range(len(cycle) - 1), key=lambda i: cycle[i])
    rotated = cycle[start:-1] + [cycle[-1]]
    return " -> ".join(rotated)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect circular imports via AST.")
    parser.add_argument("root", nargs="?", default=".", help="Project root (default: cwd)")
    parser.add_argument("--exclude", nargs="*", default=[], help="Extra dirs to skip")
    parser.add_argument(
        "--include-lazy",
        action="store_true",
        help="也把函数体内 / TYPE_CHECKING 守卫内的 import 计入。默认忽略，"
             "因为延迟导入（lazy import）本来就是用来断环的手段，不是缺陷。",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[error] root is not a directory: {root}", file=sys.stderr)
        return 2

    excludes = DEFAULT_EXCLUDES | set(args.exclude)
    graph, skipped_lazy = build_graph(root, excludes, include_lazy=args.include_lazy)
    cycles = find_cycles(graph)

    print(f"Scanned {len(graph)} modules under {root}")
    if skipped_lazy and not args.include_lazy:
        print(
            f"Ignored {skipped_lazy} lazy import(s) (function-scoped or under "
            f"TYPE_CHECKING) — pass --include-lazy to audit them too."
        )
    if not cycles:
        print("OK: no circular imports detected.")
        return 0

    print(f"\nFound {len(cycles)} circular import cycle(s):\n")
    for i, cyc in enumerate(cycles, 1):
        print(f"  [{i}] {_render(cyc)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

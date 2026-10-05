#!/usr/bin/env python
"""coverage_gaps.py — 把 coverage.json 的「缺失行号」反查成「函数/方法清单」。

用法:
    python -m pytest --cov --cov-report=json -q     # 先生成 coverage.json
    python coverage_gaps.py [coverage.json]

输出:
    - 控制台表格: 文件 | 函数 | 定义行 | 缺失行 | 覆盖状态(FULL/PARTIAL)
    - coverage_gaps.md: 同样的 Markdown 表格，可直接贴进文档

为什么需要它: coverage 只给行号，不给函数名。AST 提供函数的
`lineno`/`end_lineno`，两者求交即可定位「哪个函数没被覆盖」。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Iterable, NamedTuple


class FuncSpan(NamedTuple):
    """一个函数/方法在源码中的位置。"""

    qualname: str          # 如 SearchService.search
    lineno: int            # def 所在行
    end_lineno: int        # 函数体最后一行
    is_nested: bool        # 是否嵌套在另一个函数内


def iter_functions(tree: ast.AST) -> Iterable[FuncSpan]:
    """按源码顺序产出所有函数/方法（含 async 与嵌套函数）。"""
    def visit(node: ast.AST, prefix: str, nested: bool) -> Iterable[FuncSpan]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                yield FuncSpan(
                    qualname=qualname,
                    lineno=child.lineno,
                    end_lineno=getattr(child, "end_lineno", child.lineno),
                    is_nested=nested,
                )
                yield from visit(child, f"{qualname}.", True)
            elif isinstance(child, ast.ClassDef):
                yield from visit(child, f"{prefix}{child.name}.", nested)

    yield from visit(tree, "", False)


def attribute(spans: list[FuncSpan], tracked: set[int], missing: set[int]
              ) -> dict[int, set[int]]:
    """把每个被统计的行归给「最内层」包含它的函数。

    不做这一步的话，嵌套闭包会造成重复计数：例如
    `service_error_handler` 的 end_lineno 覆盖了内部 `decorate` 的函数体，
    同一条缺失行会同时记到父子两个函数上。

    返回 {span 下标: 该函数负责的行集合}。
    """
    owner: dict[int, set[int]] = {}
    by_index = {i: span for i, span in enumerate(spans)}
    for line in tracked:
        candidates = [
            i for i, s in by_index.items() if s.lineno < line <= s.end_lineno
        ]
        if not candidates:
            continue  # 模块级语句（不在任何函数内）
        # 起点最靠后的那个 = 最内层
        innermost = max(candidates, key=lambda i: by_index[i].lineno)
        owner.setdefault(innermost, set()).add(line)
    return owner


def analyze(coverage_json: Path, root: Path) -> list[dict[str, Any]]:
    """交叉 coverage.json 与 AST，返回每个函数的覆盖状态。"""
    data = json.loads(coverage_json.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []

    for rel_path, info in data.get("files", {}).items():
        abs_path = (root / rel_path).resolve()
        if not abs_path.is_file():
            print(f"[warn] 源文件不存在，跳过: {rel_path}", file=sys.stderr)
            continue
        try:
            tree = ast.parse(abs_path.read_text(encoding="utf-8"), filename=str(abs_path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            print(f"[warn] 无法解析 {rel_path}: {exc}", file=sys.stderr)
            continue

        executed = set(info.get("executed_lines", []))
        missing = set(info.get("missing_lines", []))
        tracked = executed | missing

        spans = list(iter_functions(tree))
        owner = attribute(spans, tracked, missing)

        for idx, body_lines in owner.items():
            miss = sorted(body_lines & missing)
            if not miss:
                continue  # 已覆盖
            span = spans[idx]
            status = "FULL" if len(miss) == len(body_lines) else "PARTIAL"
            rows.append({
                "file": rel_path.replace("\\", "/"),
                "qualname": span.qualname,
                "lineno": span.lineno,
                "end_lineno": span.end_lineno,
                "missing": miss,
                "status": status,
                "missing_count": len(miss),
                "is_nested": span.is_nested,
            })

    rows.sort(key=lambda r: (r["status"] != "FULL", r["file"], r["lineno"]))
    return rows


def render_markdown(rows: list[dict[str, Any]], total_line: str) -> str:
    """渲染 Markdown 表格。"""
    lines = [total_line, "", "| 文件名 | 函数名 | 行号 | 缺失行数 | 状态 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| `{r['file']}` | `{r['qualname']}` | {r['lineno']} | "
            f"{r['missing_count']} | {r['status']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Map coverage gaps to function names.")
    parser.add_argument("coverage_json", nargs="?", default="coverage.json")
    parser.add_argument("--root", default=".", help="源码根目录 (default: cwd)")
    parser.add_argument("--out", default="coverage_gaps.md", help="Markdown 输出路径")
    args = parser.parse_args(argv)

    cov_path = Path(args.coverage_json)
    if not cov_path.is_file():
        print(f"[error] 找不到 {cov_path}；先跑 pytest --cov --cov-report=json", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    data = json.loads(cov_path.read_text(encoding="utf-8"))
    totals = data.get("totals", {})
    total_line = (
        f"总计: {totals.get('percent_covered', 0):.1f}% "
        f"({totals.get('covered_lines', 0)}/{totals.get('num_statements', 0)} 语句, "
        f"分支 {totals.get('covered_branches', 0)}/{totals.get('num_branches', 0)})"
    )

    rows = analyze(cov_path, root)
    full = [r for r in rows if r["status"] == "FULL"]
    partial = [r for r in rows if r["status"] == "PARTIAL"]

    print(total_line)
    print(f"未覆盖函数: {len(full)} 个（完全未执行） / {len(partial)} 个（部分未执行）\n")
    for r in rows:
        mark = "XX" if r["status"] == "FULL" else "  "
        print(f"{mark} {r['file']}:{r['lineno']:<4} {r['qualname']:<48} "
              f"missing={r['missing_count']:<3} {r['missing'][:12]}")

    out = Path(args.out)
    out.write_text(render_markdown(rows, total_line), encoding="utf-8")
    print(f"\nMarkdown 已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

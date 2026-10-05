"""viz/barplot.py — Top 差异基因条形图（上调红 / 下调绿）。

条色直接来自 ``cfg.color_for_category``，尺寸/DPI/字体/线宽/网格取自
:mod:`viz.config`。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Sequence

from viz.config import VizConfig, apply_matplotlib_rcparams, get_config, new_axes, save_figure

if TYPE_CHECKING:  # pragma: no cover
    import matplotlib.figure

__all__ = ["plot_top_genes_bar"]


def plot_top_genes_bar(
    labels: Sequence[str],
    values: Sequence[float],
    *,
    values_are_lfc: bool = True,
    title: str = "Top Differentially Expressed Genes",
    xlabel: str | None = None,
    config: VizConfig | None = None,
    ax: Any = None,
    save_path: str | os.PathLike[str] | None = None,
) -> matplotlib.figure.Figure:
    """绘制水平条形图并返回 ``Figure``。

    ``values_are_lfc=True`` 时按正负自动上红/下绿色；否则统一用 ``color_up``。
    """
    cfg = config or get_config()
    apply_matplotlib_rcparams(cfg)

    names = [str(n) for n in labels]
    vals = [float(v) for v in values]
    if len(names) != len(vals):
        raise ValueError(f"labels/values 长度不一致：{len(names)} != {len(vals)}")
    if not names:
        raise ValueError("数据为空，无法绘制条形图")

    order = sorted(range(len(vals)), key=lambda i: vals[i])
    names = [names[i] for i in order]
    vals = [vals[i] for i in order]

    if values_are_lfc:
        colors = [cfg.color_for_category("up" if v > 0 else ("down" if v < 0 else "ns"))
                  for v in vals]
    else:
        colors = [cfg.color_up] * len(vals)

    if ax is None:
        fig, ax = new_axes(cfg)
    else:
        fig = ax.figure

    y_pos = list(range(len(vals)))
    ax.barh(y_pos, vals, color=colors, alpha=cfg.scatter_alpha,
            edgecolor="none", height=0.72)
    ax.axvline(0.0, color=cfg.threshold_color, linewidth=cfg.line_width * 0.8)
    ax.set_yticks(y_pos, labels=names, fontsize=cfg.tick_font_size)
    ax.set_xlabel(xlabel or (r"$\log_2$ Fold Change" if values_are_lfc else "Value"),
                  fontsize=cfg.label_font_size)
    ax.set_title(title, fontsize=cfg.title_font_size, color=cfg.text_color)
    ax.grid(True, axis="x", alpha=cfg.grid_alpha, color=cfg.grid_color,
            linestyle=cfg.grid_linestyle, linewidth=0.5)

    fig.tight_layout()
    if save_path is not None:
        save_figure(fig, save_path, cfg=cfg)
    return fig

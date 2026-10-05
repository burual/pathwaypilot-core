"""viz/ma_plot.py — MA 图（A = log2(baseMean)，M = log2FC）。

视觉参数（尺寸/DPI/字体/三色/线宽/网格透明度）一律来自 :mod:`viz.config`，
本模块不含任何硬编码字面量。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Sequence

from viz.config import VizConfig, apply_matplotlib_rcparams, get_config, new_axes, save_figure
from viz.volcano import DEFAULT_LFC_CUTOFF, DEFAULT_PADJ_CUTOFF, classify

if TYPE_CHECKING:  # pragma: no cover
    import matplotlib.figure

__all__ = ["plot_ma"]


def plot_ma(
    base_mean: Sequence[float],
    lfc: Sequence[float],
    padj: Sequence[float] | None = None,
    *,
    lfc_cutoff: float = DEFAULT_LFC_CUTOFF,
    padj_cutoff: float = DEFAULT_PADJ_CUTOFF,
    title: str = "MA Plot",
    config: VizConfig | None = None,
    ax: Any = None,
    save_path: str | os.PathLike[str] | None = None,
) -> matplotlib.figure.Figure:
    """绘制 MA 图并返回 ``Figure``。``padj`` 省略时仅按 ``lfc_cutoff`` 着色。"""
    import numpy as np

    cfg = config or get_config()
    apply_matplotlib_rcparams(cfg)

    a = np.log2(np.clip(np.asarray(base_mean, dtype=float), 1e-6, None))
    m = np.asarray(lfc, dtype=float)
    if len(a) != len(m):
        raise ValueError(f"base_mean/lfc 长度不一致：{len(a)} != {len(m)}")
    if not len(a):
        raise ValueError("数据为空，无法绘制 MA 图")

    if padj is not None:
        p = np.asarray(padj, dtype=float)
        cats = np.array([classify(float(x), float(q), padj_cutoff=padj_cutoff,
                                  lfc_cutoff=lfc_cutoff) for x, q in zip(m, p, strict=False)])
    else:
        cats = np.where(m > lfc_cutoff, "up", np.where(m < -lfc_cutoff, "down", "ns"))

    if ax is None:
        fig, ax = new_axes(cfg)
    else:
        fig = ax.figure

    for cat, label in (("ns", "NS"), ("up", "Up"), ("down", "Down")):
        mask = cats == cat
        if not mask.any():
            continue
        ax.scatter(a[mask], m[mask],
                   s=cfg.marker_size if cat != "ns" else cfg.marker_size * 0.6,
                   c=cfg.color_for_category(cat),
                   alpha=cfg.scatter_alpha if cat != "ns" else cfg.scatter_alpha * 0.6,
                   edgecolors="none", label=f"{label} ({int(mask.sum())})")

    ax.axhline(0.0, color=cfg.threshold_color, linewidth=cfg.line_width * 0.8)
    for y in (lfc_cutoff, -lfc_cutoff):
        ax.axhline(y, color=cfg.threshold_color, linestyle=cfg.grid_linestyle,
                   linewidth=cfg.line_width * 0.6, alpha=0.8)

    ax.set_title(title, fontsize=cfg.title_font_size, color=cfg.text_color)
    ax.set_xlabel(r"$\log_2(\mathrm{baseMean})$", fontsize=cfg.label_font_size)
    ax.set_ylabel(r"$\log_2$ Fold Change (M)", fontsize=cfg.label_font_size)
    ax.grid(True, alpha=cfg.grid_alpha, color=cfg.grid_color,
            linestyle=cfg.grid_linestyle, linewidth=0.5)
    ax.legend(fontsize=cfg.legend_font_size, loc="upper right")

    fig.tight_layout()
    if save_path is not None:
        save_figure(fig, save_path, cfg=cfg)
    return fig

"""viz/correlation.py — 样本间 Pearson 相关性热图（含系数标注）。

配色沿用规范的红-白-蓝双向渐变（正相关红、负相关蓝），字号/DPI/底色/线宽
来自 :mod:`viz.config`。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Sequence

from viz.config import VizConfig, apply_matplotlib_rcparams, get_config, new_axes, save_figure

if TYPE_CHECKING:  # pragma: no cover
    import matplotlib.figure

__all__ = ["plot_correlation_heatmap"]


def plot_correlation_heatmap(
    matrix: Sequence[Sequence[float]],
    *,
    sample_labels: Sequence[str] | None = None,
    annotate: bool = True,
    title: str = "Sample Correlation Matrix",
    cmap: str = "RdBu_r",
    config: VizConfig | None = None,
    ax: Any = None,
    save_path: str | os.PathLike[str] | None = None,
) -> matplotlib.figure.Figure:
    """绘制样本相关性热图并返回 ``Figure``。

    ``matrix`` 每**行**一个样本、每列一个基因；样本数 < 2 或列数 < 2 时无法算相关，
    会抛 ``ValueError``。
    """
    import numpy as np

    cfg = config or get_config()
    apply_matplotlib_rcparams(cfg)

    arr = np.asarray(matrix, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"matrix 应为二维，当前 ndim={arr.ndim}")
    if arr.shape[0] < 2:
        raise ValueError(f"样本数需≥2 才能计算相关性，当前 {arr.shape[0]}")
    if arr.shape[1] < 2:
        raise ValueError(f"特征数需≥2 才能计算相关性，当前 {arr.shape[1]}")

    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(arr)
    corr = np.nan_to_num(corr, nan=0.0)

    n = corr.shape[0]
    labels = list(sample_labels) if sample_labels is not None else [f"S{i + 1}" for i in range(n)]
    if len(labels) != n:
        raise ValueError(f"sample_labels 数量({len(labels)})与样本数({n})不一致")

    if ax is None:
        fig, ax = new_axes(cfg)
    else:
        fig = ax.figure

    image = ax.imshow(corr, cmap=cmap, vmin=-1.0, vmax=1.0)
    ax.set_xticks(range(n), labels=labels, fontsize=cfg.tick_font_size, rotation=90)
    ax.set_yticks(range(n), labels=labels, fontsize=cfg.tick_font_size)
    ax.set_title(title, fontsize=cfg.title_font_size, color=cfg.text_color)
    ax.grid(False)

    if annotate:
        for i in range(n):
            for j in range(n):
                value = float(corr[i, j])
                ax.text(j, i, f"{value:.2f}", ha="center", va="center",
                        fontsize=cfg.tick_font_size,
                        color=cfg.background_color if abs(value) > 0.6 else cfg.text_color)

    colorbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    colorbar.set_label("Pearson r", fontsize=cfg.label_font_size, color=cfg.text_color)
    colorbar.ax.tick_params(labelsize=cfg.tick_font_size, colors=cfg.text_color)

    fig.tight_layout()
    if save_path is not None:
        save_figure(fig, save_path, cfg=cfg)
    return fig

"""viz/heatmap.py — 表达矩阵热图（z-score 着色 + 聚类排序）。

尺寸/DPI/字体/底色/网格一律来自 :mod:`viz.config`。
集群（cmap）与是否聚类属于"图语义"，保留为入参。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Sequence

from viz.config import VizConfig, apply_matplotlib_rcparams, get_config, new_axes, save_figure

if TYPE_CHECKING:  # pragma: no cover
    import matplotlib.figure

__all__ = ["plot_heatmap", "zscore_rows"]


def zscore_rows(matrix: Any) -> Any:
    """按行做 z-score（列 = 样本）。零方差行返回全 0，避免除零。"""
    import numpy as np

    arr = np.asarray(matrix, dtype=float)
    mean = np.nanmean(arr, axis=1, keepdims=True)
    std = np.nanstd(arr, axis=1, keepdims=True)
    return np.divide(arr - mean, std, out=np.zeros_like(arr), where=std > 1e-12)


def _cluster_order(matrix: Any) -> list[int]:
    """用 scipy 做 ward 聚类排序；scipy 缺失或无数据时退回原顺序。"""
    try:
        from scipy.cluster.hierarchy import leaves_list, linkage
    except ImportError:  # pragma: no cover - 可选依赖
        return list(range(matrix.shape[0]))
    if matrix.shape[0] <= 2:
        return list(range(matrix.shape[0]))
    try:
        return list(map(int, leaves_list(linkage(matrix, method="ward"))))
    except Exception:  # pragma: no cover - 数值退化
        return list(range(matrix.shape[0]))


def plot_heatmap(
    matrix: Sequence[Sequence[float]],
    *,
    row_labels: Sequence[str] | None = None,
    col_labels: Sequence[str] | None = None,
    zscore: bool = True,
    cluster_rows: bool = False,
    cmap: str = "RdBu_r",
    colorbar_label: str = "Z-score",
    title: str = "Heatmap",
    config: VizConfig | None = None,
    ax: Any = None,
    save_path: str | os.PathLike[str] | None = None,
) -> matplotlib.figure.Figure:
    """绘制热图并返回 ``Figure``。

    ``zscore=True`` 时按行标准化（基因间可比），``cluster_rows=True`` 时按 ward
    聚类重排行序（需 scipy，缺失自动跳过）。
    """
    import numpy as np

    cfg = config or get_config()
    apply_matplotlib_rcparams(cfg)

    data = np.asarray(matrix, dtype=float)
    if data.ndim != 2:
        raise ValueError(f"matrix 应为二维，当前 ndim={data.ndim}")
    if data.size == 0:
        raise ValueError("矩阵为空，无法绘制热图")

    if zscore:
        data = zscore_rows(data)

    if cluster_rows:
        order = _cluster_order(data)
        data = data[order]
        if row_labels is not None:
            row_labels = [row_labels[i] for i in order]

    if row_labels is None:
        row_labels = [f"R{i}" for i in range(data.shape[0])]
    if col_labels is None:
        col_labels = [f"C{j}" for j in range(data.shape[1])]

    if ax is None:
        fig, ax = new_axes(cfg)
    else:
        fig = ax.figure

    limit = float(np.nanmax(np.abs(data))) or 1.0
    image = ax.imshow(data, aspect="auto", cmap=cmap, vmin=-limit, vmax=limit)

    ax.set_xticks(range(data.shape[1]), labels=list(col_labels),
                  fontsize=cfg.tick_font_size, rotation=90)
    ax.set_yticks(range(data.shape[0]), labels=list(row_labels),
                  fontsize=cfg.tick_font_size)
    ax.set_title(title, fontsize=cfg.title_font_size, color=cfg.text_color)
    # 热图本身即内容，关掉网格避免压在格子上
    ax.grid(False)

    colorbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    colorbar.set_label(colorbar_label, fontsize=cfg.label_font_size, color=cfg.text_color)
    colorbar.ax.tick_params(labelsize=cfg.tick_font_size, colors=cfg.text_color)

    fig.tight_layout()
    if save_path is not None:
        save_figure(fig, save_path, cfg=cfg)
    return fig

"""viz/pca_plot.py — PCA / t-SNE / UMAP 降维散点图（按分组着色）。

分组配色取自 :attr:`VizConfig.palette`（循环取用），点大小/透明度/字号/DPI
均来自 :mod:`viz.config`。降维本身若缺 scikit-learn 会明确报错，不做静默降级。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Sequence

from viz.config import VizConfig, apply_matplotlib_rcparams, get_config, new_axes, save_figure

if TYPE_CHECKING:  # pragma: no cover
    import matplotlib.figure

__all__ = ["plot_embedding", "plot_pca"]


def plot_embedding(
    coords: Sequence[Sequence[float]],
    *,
    groups: Sequence[str] | None = None,
    labels: Sequence[str] | None = None,
    axis_labels: tuple[str, str] = ("PC1", "PC2"),
    subtitle: str = "",
    title: str = "Embedding",
    annotate: bool = False,
    config: VizConfig | None = None,
    ax: Any = None,
    save_path: str | os.PathLike[str] | None = None,
) -> matplotlib.figure.Figure:
    """绘制二维嵌入散点（``coords`` 形如 ``[[x, y], ...]``）。"""
    import numpy as np

    cfg = config or get_config()
    apply_matplotlib_rcparams(cfg)

    xy = np.asarray(coords, dtype=float)
    if xy.ndim != 2 or xy.shape[1] < 2:
        raise ValueError(f"coords 应为 (n, 2)，当前形状 {xy.shape}")
    if not len(xy):
        raise ValueError("坐标为空，无法绘图")

    if ax is None:
        fig, ax = new_axes(cfg)
    else:
        fig = ax.figure

    if groups is None:
        ax.scatter(xy[:, 0], xy[:, 1], s=cfg.marker_size * 1.6, c=cfg.color_up,
                   alpha=cfg.scatter_alpha, edgecolors=cfg.background_color,
                   linewidths=0.6)
    else:
        unique = list(dict.fromkeys(str(g) for g in groups))
        colors = cfg.palette_colors(len(unique))
        for idx, group in enumerate(unique):
            mask = np.array([str(g) == group for g in groups])
            ax.scatter(xy[mask, 0], xy[mask, 1], s=cfg.marker_size * 1.6,
                       c=colors[idx], alpha=cfg.scatter_alpha,
                       edgecolors=cfg.background_color, linewidths=0.6, label=str(group))
        ax.legend(fontsize=cfg.legend_font_size, loc="best")

    if annotate and labels is not None:
        for (x, y), name in zip(xy, labels, strict=False):
            ax.annotate(str(name), (x, y), textcoords="offset points",
                        xytext=(4, 4), fontsize=cfg.tick_font_size, color=cfg.text_color)

    heading = f"{title}\n{subtitle}" if subtitle else title
    ax.set_title(heading, fontsize=cfg.title_font_size, color=cfg.text_color)
    ax.set_xlabel(axis_labels[0], fontsize=cfg.label_font_size)
    ax.set_ylabel(axis_labels[1], fontsize=cfg.label_font_size)
    ax.grid(True, alpha=cfg.grid_alpha, color=cfg.grid_color,
            linestyle=cfg.grid_linestyle, linewidth=0.5)

    fig.tight_layout()
    if save_path is not None:
        save_figure(fig, save_path, cfg=cfg)
    return fig


def plot_pca(
    matrix: Sequence[Sequence[float]],
    *,
    groups: Sequence[str] | None = None,
    labels: Sequence[str] | None = None,
    title: str = "PCA Plot",
    config: VizConfig | None = None,
    ax: Any = None,
    save_path: str | os.PathLike[str] | None = None,
) -> matplotlib.figure.Figure:
    """对 ``matrix``（每行一个样本）做 PCA 并绘制前两个主成分。"""
    import numpy as np

    try:
        from sklearn.decomposition import PCA
    except ImportError as exc:  # pragma: no cover - 可选依赖
        raise ImportError("plot_pca 需要 scikit-learn（pip install scikit-learn）") from exc

    arr = np.asarray(matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 2:
        raise ValueError(f"matrix 应为 (n_samples, n_features) 且样本数≥2，当前 {arr.shape}")

    col_mean = np.nanmean(arr, axis=0)
    filled = np.where(np.isnan(arr), col_mean, arr)
    scaled = (filled - filled.mean(axis=0)) / (filled.std(axis=0) + 1e-10)

    reducer = PCA(n_components=2)
    coords = reducer.fit_transform(scaled)
    var = reducer.explained_variance_ratio_
    subtitle = f"PC1: {var[0]:.1%}, PC2: {var[1]:.1%}"

    return plot_embedding(
        coords, groups=groups, labels=labels,
        axis_labels=("PC1", "PC2"), subtitle=subtitle, title=title,
        config=config, ax=ax, save_path=save_path,
    )

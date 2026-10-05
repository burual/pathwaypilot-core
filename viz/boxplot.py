"""viz/boxplot.py — 分组表达箱线图（Ctrl vs Treat）。

分组配色复用语义色：Control = ``color_ns``，Treatment = 该基因的 up/down 色。
尺寸/DPI/字体/线宽/网格透明度来自 :mod:`viz.config`。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from viz.config import VizConfig, apply_matplotlib_rcparams, get_config, new_axes, save_figure

if TYPE_CHECKING:  # pragma: no cover
    import matplotlib.figure

__all__ = ["plot_expression_boxplot"]


def plot_expression_boxplot(
    samples: Mapping[str, Sequence[float]],
    *,
    gene_values: Mapping[str, float] | None = None,
    title: str = "Expression Distribution",
    ylabel: str = "Expression",
    config: VizConfig | None = None,
    ax: Any = None,
    save_path: str | os.PathLike[str] | None = None,
) -> matplotlib.figure.Figure:
    """绘制按基因分组的箱线图。

    Parameters
    ----------
    samples:
        ``{"TP53_ctrl": [..], "TP53_treat": [..], ...}`` —— 键以 ``_ctrl`` /
        ``_treat`` 结尾区分组别；同一基因的 ctrl/treat 成对出现时并排摆放。
    gene_values:
        ``{"TP53": 1.8}`` 该基因的 log2FC，用于给 treatment 箱体着色
        （>0 红 / <0 绿 / 缺失灰）。
    """
    cfg = config or get_config()
    apply_matplotlib_rcparams(cfg)

    if not samples:
        raise ValueError("samples 为空，无法绘制箱线图")

    # 归并成 {gene: {"ctrl": [...], "treat": [...]}}
    grouped: dict[str, dict[str, list[float]]] = {}
    for key, values in samples.items():
        if key.endswith("_ctrl"):
            gene, role = key[: -len("_ctrl")], "ctrl"
        elif key.endswith("_treat"):
            gene, role = key[: -len("_treat")], "treat"
        else:
            gene, role = key, "ctrl"
        grouped.setdefault(gene, {})[role] = [float(v) for v in values]

    genes = list(grouped)
    gene_values = gene_values or {}

    if ax is None:
        fig, ax = new_axes(cfg)
    else:
        fig = ax.figure

    positions: list[float] = []
    datasets: list[list[float]] = []
    colors: list[str] = []
    tick_pos: list[float] = []
    tick_names: list[str] = []
    offset = 0.18

    for i, gene in enumerate(genes):
        base = i + 1
        tick_pos.append(base)
        tick_names.append(gene)
        roles = grouped[gene]
        if "ctrl" in roles and "treat" in roles:
            positions += [base - offset, base + offset]
            datasets += [roles["ctrl"], roles["treat"]]
            lfc = float(gene_values.get(gene, 0.0))
            treat_color = cfg.color_up if lfc > 0 else (cfg.color_down if lfc < 0 else cfg.color_ns)
            colors += [cfg.color_ns, treat_color]
        elif "ctrl" in roles:
            positions.append(base)
            datasets.append(roles["ctrl"])
            colors.append(cfg.color_ns)
        else:
            positions.append(base)
            datasets.append(roles["treat"])
            lfc = float(gene_values.get(gene, 0.0))
            colors.append(cfg.color_up if lfc > 0 else (cfg.color_down if lfc < 0 else cfg.color_ns))

    box = ax.boxplot(
        datasets, positions=positions, widths=0.3, patch_artist=True,
        medianprops={"color": cfg.text_color, "linewidth": cfg.line_width},
        whiskerprops={"color": cfg.threshold_color, "linewidth": cfg.line_width * 0.8},
        capprops={"color": cfg.threshold_color, "linewidth": cfg.line_width * 0.8},
        flierprops={"markersize": 3, "markerfacecolor": cfg.color_ns,
                    "markeredgecolor": cfg.color_ns},
    )
    for patch, color in zip(box["boxes"], colors, strict=False):
        patch.set_facecolor(color)
        patch.set_alpha(cfg.scatter_alpha)
        patch.set_edgecolor(cfg.threshold_color)

    ax.set_xticks(tick_pos, labels=tick_names, fontsize=cfg.tick_font_size)
    ax.set_title(title, fontsize=cfg.title_font_size, color=cfg.text_color)
    ax.set_ylabel(ylabel, fontsize=cfg.label_font_size)
    ax.grid(True, axis="y", alpha=cfg.grid_alpha, color=cfg.grid_color,
            linestyle=cfg.grid_linestyle, linewidth=0.5)

    fig.tight_layout()
    if save_path is not None:
        save_figure(fig, save_path, cfg=cfg)
    return fig

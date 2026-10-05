"""viz/venn.py — 2~3 组基因重叠 Venn 图（纯 matplotlib 绘制，无第三方依赖）。

圆的尺寸/DPI/字体/分组配色（``cfg.palette_colors``）/底色均来自 :mod:`viz.config`。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from viz.config import VizConfig, apply_matplotlib_rcparams, get_config, new_axes, save_figure

if TYPE_CHECKING:  # pragma: no cover
    import matplotlib.figure

__all__ = ["plot_venn", "venn_counts"]


def venn_counts(gene_sets: Mapping[str, Sequence[str]]) -> dict[frozenset[str], int]:
    """计算重叠计数：键为组名 frozenset，值为该组合的专属基因数。

    例如 ``{frozenset({"A"}): 12, frozenset({"A", "B"}): 5, ...}``。
    """
    names = list(gene_sets)
    membership: dict[str, frozenset[str]] = {}
    for name in names:
        for gene in set(gene_sets[name]):
            membership[str(gene)] = membership.get(str(gene), frozenset()) | {name}

    counts: dict[frozenset[str], int] = {}
    for combo in membership.values():
        if combo:
            counts[combo] = counts.get(combo, 0) + 1
    return counts


def plot_venn(
    gene_sets: Mapping[str, Sequence[str]],
    *,
    title: str = "DEG Overlap",
    config: VizConfig | None = None,
    ax: Any = None,
    save_path: str | os.PathLike[str] | None = None,
) -> matplotlib.figure.Figure:
    """绘制 2~3 组 Venn 图并返回 ``Figure``。

    4 组及以上不绘制（会抛 ``ValueError``）—— 那类图应交由专门的
    4-set Venn / UpSetPlot 处理，硬塞进圆形构型只会更难读。
    """
    from matplotlib.patches import Circle

    cfg = config or get_config()
    apply_matplotlib_rcparams(cfg)

    names = list(gene_sets)
    if not 2 <= len(names) <= 3:
        raise ValueError(f"Venn 图仅支持 2~3 组，当前 {len(names)} 组")

    counts = venn_counts(gene_sets)
    colors = cfg.palette_colors(len(names))

    if ax is None:
        fig, ax = new_axes(cfg)
    else:
        fig = ax.figure

    if len(names) == 2:
        centers = {names[0]: (-0.35, 0.0), names[1]: (0.35, 0.0)}
        radius = 0.62
    else:
        centers = {names[0]: (0.0, 0.36), names[1]: (-0.36, -0.28), names[2]: (0.36, -0.28)}
        radius = 0.6

    for idx, name in enumerate(names):
        cx, cy = centers[name]
        ax.add_patch(Circle((cx, cy), radius, facecolor=colors[idx],
                            alpha=0.28, edgecolor=colors[idx], linewidth=cfg.line_width))
        # 组名放在各自圆的外侧
        ax.text(cx, cy + radius + 0.06, str(name), ha="center", va="bottom",
                fontsize=cfg.label_font_size, color=cfg.text_color)

    # 数字标注位置：组合成员质心；单组用圆心附近
    for combo, count in counts.items():
        members = list(combo)
        if len(members) == 1:
            cx, cy = centers[members[0]]
        else:
            cx = sum(centers[m][0] for m in members) / len(members)
            cy = sum(centers[m][1] for m in members) / len(members)
        ax.text(cx, cy, str(count), ha="center", va="center",
                fontsize=cfg.font_size, color=cfg.text_color)

    ax.set_title(title, fontsize=cfg.title_font_size, color=cfg.text_color)
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.05, 1.15)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.grid(False)

    fig.tight_layout()
    if save_path is not None:
        save_figure(fig, save_path, cfg=cfg)
    return fig

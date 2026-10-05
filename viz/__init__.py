"""viz — 统一可视化配置中心 + 8 个绘图模块。

设计原则：**视觉参数只有一个来源**（:mod:`viz.config`）。

    绘图模块（8 个）──读──▶ viz.config.get_config()  ◀──覆盖── VIZ_* 环境变量

本包入口采用 **PEP 562 懒加载**（`__getattr__`），有两个好处：

1. ``import viz`` 不预先拉起任何子模块，也不拉起 matplotlib ——
   配置可以在纯逻辑环境（CI 的 lint / 单测）里安全读取；
2. 用 ``python -m viz.volcano`` 直接跑某张图时，不会因"包已导入该子模块、
   随后又要以 ``__main__`` 执行它"而触发 runpy 的 RuntimeWarning。
   （与本仓 ``app/__init__.py`` 的懒加载约定一致。）

对外 API 仍与急切导入完全等价：``from viz import plot_volcano``、
``viz.get_config()`` 都能用。

8 个绘图模块：

===============  ==========================  =====================================
模块              函数                        说明
===============  ==========================  =====================================
``volcano``      ``plot_volcano``            火山图（上红/下绿/不显著灰）
``ma_plot``      ``plot_ma``                 MA 图（A=log2 baseMean）
``heatmap``      ``plot_heatmap``            表达矩阵热图（z-score + 可选聚类）
``pca_plot``     ``plot_embedding`` /        PCA / t-SNE / UMAP 降维散点
                 ``plot_pca``
``boxplot``      ``plot_expression_boxplot`` 分组表达箱线图
``barplot``      ``plot_top_genes_bar``      Top 差异基因条形图
``venn``         ``plot_venn``               2~3 组重叠 Venn 图
``correlation``  ``plot_correlation_heatmap`` 样本相关性热图
===============  ==========================  =====================================

用法::

    from viz import get_config, plot_volcano

    cfg = get_config()                       # 读 VIZ_* 环境变量（含默认值）
    plot_volcano(lfc, padj, save_path="volcano.png")
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__version__ = "1.0.0"

#: 导出名 -> 定义它的子模块。懒加载据此按需 import。
_LAZY_EXPORTS: dict[str, str] = {
    # ── 配置中心 ──
    "VizConfig": "viz.config",
    "get_config": "viz.config",
    "reset_config": "viz.config",
    "config_override": "viz.config",
    "apply_matplotlib_rcparams": "viz.config",
    "new_axes": "viz.config",
    "save_figure": "viz.config",
    "ENV_PREFIX": "viz.config",
    "DEFAULT_PALETTE": "viz.config",
    "DEFAULT_FONT_FALLBACKS": "viz.config",
    # ── 8 个绘图模块 ──
    "classify": "viz.volcano",
    "plot_volcano": "viz.volcano",
    "volcano_figure": "viz.volcano",
    "volcano_from_records": "viz.volcano",
    "VolcanoResult": "viz.volcano",
    "VolcanoStats": "viz.volcano",
    "plot_ma": "viz.ma_plot",
    "plot_heatmap": "viz.heatmap",
    "zscore_rows": "viz.heatmap",
    "plot_embedding": "viz.pca_plot",
    "plot_pca": "viz.pca_plot",
    "plot_expression_boxplot": "viz.boxplot",
    "plot_top_genes_bar": "viz.barplot",
    "plot_venn": "viz.venn",
    "venn_counts": "viz.venn",
    "plot_correlation_heatmap": "viz.correlation",
}

__all__ = [
    "DEFAULT_FONT_FALLBACKS",
    "DEFAULT_PALETTE",
    "ENV_PREFIX",
    "VizConfig",
    "VolcanoResult",
    "VolcanoStats",
    "apply_matplotlib_rcparams",
    "classify",
    "config_override",
    "get_config",
    "new_axes",
    "plot_correlation_heatmap",
    "plot_embedding",
    "plot_expression_boxplot",
    "plot_heatmap",
    "plot_ma",
    "plot_pca",
    "plot_top_genes_bar",
    "plot_venn",
    "plot_volcano",
    "reset_config",
    "save_figure",
    "venn_counts",
    "volcano_figure",
    "volcano_from_records",
    "zscore_rows",
]


def __getattr__(name: str) -> Any:
    """PEP 562：按需从子模块取属性，取到后写回 ``globals()`` 缓存。"""
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:  # 仅供静态分析/IDE 补全；运行时不执行
    from viz.barplot import plot_top_genes_bar
    from viz.boxplot import plot_expression_boxplot
    from viz.config import (
        DEFAULT_FONT_FALLBACKS,
        DEFAULT_PALETTE,
        ENV_PREFIX,
        VizConfig,
        apply_matplotlib_rcparams,
        config_override,
        get_config,
        new_axes,
        reset_config,
        save_figure,
    )
    from viz.correlation import plot_correlation_heatmap
    from viz.heatmap import plot_heatmap, zscore_rows
    from viz.ma_plot import plot_ma
    from viz.pca_plot import plot_embedding, plot_pca
    from viz.venn import plot_venn, venn_counts
    from viz.volcano import (
        VolcanoResult,
        VolcanoStats,
        classify,
        plot_volcano,
        volcano_figure,
        volcano_from_records,
    )

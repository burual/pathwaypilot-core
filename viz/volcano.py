"""viz/volcano.py — 火山图：静态 PNG（matplotlib）+ 可选交互 HTML（plotly）。

## 为什么是"双通道"而不是"二选一"

静态 PNG 与交互 HTML 服务的是**两类完全不同的消费者**，谁也不能替代谁：

* 静态 PNG —— 论文插图、邮件附件、报告 PDF、服务端缩略图：要求确定性、
  体积小、不依赖 JS；
* 交互 HTML —— 网页探索：要求悬停看基因名、框选放大、点选高亮，这些**只有**
  能在客户端跑的图才能做到。

所以本模块不把 matplotlib 换成 plotly，而是让二者共用**同一份**分类结果、
阈值、配色与统计 —— 视觉参数仍全部来自 :mod:`viz.config`，两个通道不会漂移。

====================================================================  ==========================  ==========================================
函数                                                                   返回                        何时用
====================================================================  ==========================  ==========================================
:func:`volcano_figure`                                                ``Figure``                  要 Figure / 要复用 ``ax`` / 组合子图
:func:`plot_volcano`                                                  ``VolcanoResult``(dict)     面向接口与前端：图 + 交互 + 统计
====================================================================  ==========================  ==========================================

## 返回值

``plot_volcano`` 返回 :class:`VolcanoResult`（普通 dict，可直接 ``json.dumps``）::

    {
        "image_base64": "...",      # 静态 PNG 的 base64（始终存在）
        "interactive_html": "...",  # 自包含 HTML（仅 interactive=True 且 plotly 可用时存在）
        "stats": {"up": 123, "down": 45, "ns": 10234, "total": 10402},
    }

``image_base64`` 是**裸 base64**（不含 ``data:image/png;base64,`` 前缀）。前端要用
``<img>`` 直接显示时自行拼接：``f"data:image/png;base64,{r['image_base64']}"``。

## 互动能力与实现手段

================================  ==========================================================
能力                               实现
================================  ==========================================================
悬停显示 gene / log2FC / p-value  静态：``mplcursors``；交互：plotly ``hovertemplate``
框选放大                            plotly ``dragmode="zoom"``（拖拽即框选）+ ``scrollZoom``
点击高亮某个基因                    注入 ``plotly_click`` 回调（``to_html(post_script=...)``）
上调/下调/不显著数量统计            静态：图例名后缀；两通道：``stats`` 字段
================================  ==========================================================

## 可选依赖及降级策略

``plotly`` 与 ``mplcursors`` 都是**可选依赖**，与 :mod:`viz.config` 的既有约定一致 ——
缺依赖时**告警并降级**，绝不因为一个可视化增强项把整条出图链路炸掉：

* ``interactive=True`` 但无 ``plotly`` → ``RuntimeWarning``，只返回
  ``image_base64`` + ``stats``（无 ``interactive_html`` 键）；
* ``hover=True`` 但无 ``mplcursors`` → ``RuntimeWarning``，图照常出，只是没有悬停。

## 与配置中心的关系

绘图逻辑（分类、阈值线、Top-N 标注、配色分组）**一行没动**，视觉量（``figsize`` /
``dpi`` / 三色 / 字号 / ``grid_alpha`` / 线宽 / 透明度）全部改读
:func:`viz.config.get_config`。两个通道都读同一份 ``VizConfig``：把 ``VIZ_DPI=300``
或 ``VIZ_COLOR_UP=#B2182B`` 设进环境变量，PNG 与 HTML 会**同时**变色。

## 边界

``padj_cutoff`` / ``lfc_cutoff`` 是**统计阈值**，不是视觉参数，故不进 ``VizConfig``，
只作为本模块的函数入参。
"""

from __future__ import annotations

import base64
import io
import math
import os
import re
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Sequence, TypedDict

# `NotRequired` 是 Python 3.11 才进入标准库的（本仓 requires-python >= 3.10）。
# ⚠️ 这类「stdlib 名字的版本可用性」ruff 抓不到 —— target-version = "py310"
#    拦得住新语法，但拦不住 `from typing import <3.11 才有的名字>`。实测它是
#    **收集阶段 ImportError**（退出码 2、覆盖率产物一个都不写），只在最低版本
#    job 上暴露；本仓 CI 的 3.10 job 就是靠这个抓出来的，别删那一档矩阵。
try:  # Python >= 3.11
    from typing import NotRequired
except ImportError:  # pragma: no cover - Python 3.10 回退
    from typing_extensions import NotRequired  # pragma: no cover

from viz.config import VizConfig, apply_matplotlib_rcparams, get_config, save_figure

if TYPE_CHECKING:  # pragma: no cover
    import matplotlib.figure

__all__ = [
    "DEFAULT_LFC_CUTOFF",
    "DEFAULT_PADJ_CUTOFF",
    "MPLCURSORS_MISSING_MSG",
    "PLOTLY_MISSING_MSG",
    "VolcanoResult",
    "VolcanoStats",
    "classify",
    "plot_volcano",
    "volcano_figure",
    "volcano_from_records",
]

#: 显著性阈值（业务/统计参数，非视觉配置）。
DEFAULT_PADJ_CUTOFF: float = 0.05
DEFAULT_LFC_CUTOFF: float = 0.585

#: 缺 plotly 时的降级提示。
PLOTLY_MISSING_MSG = (
    "interactive=True 需要可选依赖 plotly（pip install plotly）；"
    "本次已降级为仅返回静态图，结果中没有 'interactive_html' 键。"
)
#: 缺 mplcursors 时的降级提示。
MPLCURSORS_MISSING_MSG = (
    "hover=True 需要可选依赖 mplcursors（pip install mplcursors）；"
    "本次已降级为无悬停标签的静态图。"
)

_CATEGORY_ORDER = ("ns", "up", "down")
_CATEGORY_LABEL = {"ns": "Not Significant", "up": "Up-regulated", "down": "Down-regulated"}

#: matplotlib mathtext -> plotly 可见纯文本的替换表（plotly 不解析 mathtext）。
_TEX_SUBS: tuple[tuple[str, str], ...] = (
    (r"\\math(?:rm|bf|it)\{([^{}]*)\}", r"\1"),
    (r"\\log_\{10\}", "log10"),
    (r"\\log_2", "log2"),
    (r"\\log", "log"),
    (r"\$", ""),
    (r"\s{2,}", " "),
)


# ═══════════════════════════════════════════════════════════════════════════
#  返回值类型
# ═══════════════════════════════════════════════════════════════════════════
class VolcanoStats(TypedDict):
    """上调 / 下调 / 不显著 / 总数的计数。"""

    up: int
    down: int
    ns: int
    total: int


class VolcanoResult(TypedDict):
    """``plot_volcano`` 的返回值。

    ``interactive_html`` 用 :data:`typing.NotRequired` 标注：只有
    ``interactive=True`` 且 plotly 可用时才存在该键 —— 这正是"可选依赖降级"
    在类型层面的诚实表达。

    ⚠️ 该可选性**只在静态检查层成立**（mypy 认）。运行时不成立：本模块有
    ``from __future__ import annotations``，注解变成字符串，TypedDict 不做求值，
    于是 ``__required_keys__`` 会把三个键全算作必需 —— 实测
    ``{'image_base64', 'interactive_html', 'stats'}`` / ``__optional_keys__ == set()``。
    根因是 PEP 563 与 TypedDict 的运行时内省不兼容（换 typing_extensions 也一样）。
    影响：**别把本类当作 FastAPI 响应模型**，那样会生成"三个字段都必填"的错误 schema。
    本仓只把它当函数返回注解用，不受影响。
    """

    image_base64: str
    stats: VolcanoStats
    interactive_html: NotRequired[str]


class _Prepared(NamedTuple):
    """分类结果与统计（两个输出通道共用，保证二者绝不漂移）。"""

    x: Any          # np.ndarray[float]  log2FC
    y: Any          # np.ndarray[float]  -log10(padj)
    padj: Any       # np.ndarray[float]  padj 原值
    cats: Any       # np.ndarray[str]    "up"/"down"/"ns"
    symbols: list[str]
    stats: VolcanoStats


# ═══════════════════════════════════════════════════════════════════════════
#  分类与数据整形
# ═══════════════════════════════════════════════════════════════════════════
def classify(lfc: float, padj: float, *,
             padj_cutoff: float = DEFAULT_PADJ_CUTOFF,
             lfc_cutoff: float = DEFAULT_LFC_CUTOFF) -> str:
    """按阈值把单个基因归入 ``"up"`` / ``"down"`` / ``"ns"``。"""
    if padj < padj_cutoff and lfc > lfc_cutoff:
        return "up"
    if padj < padj_cutoff and lfc < -lfc_cutoff:
        return "down"
    return "ns"


def _record_getter(row: Any) -> Any:
    """返回「按 key 取值」的访问器：dict 走 ``.get``，具名对象走 ``getattr``。

    抽成独立函数是为了让 :func:`volcano_from_records` 的循环体保持扁平 ——
    行内 ``if/else`` 赋值会触发 SIM108，而内联 lambda 三元式可读性明显更差。
    """
    if isinstance(row, dict):
        return row.get
    return lambda key, _row=row: getattr(_row, key, None)


def volcano_from_records(
    records: Sequence[Any],
    *,
    lfc_key: str = "log2FoldChange",
    padj_key: str = "padj",
    label_key: str = "gene_id",
) -> tuple[list[float], list[float], list[str]]:
    """把 ``[{gene_id, log2FoldChange, padj}, ...]`` 拆成 ``(lfc, padj, labels)``。

    同时兼容 dict / 具名对象（用 ``getattr`` 兜底）。
    """
    lfc: list[float] = []
    padj: list[float] = []
    labels: list[str] = []
    for row in records:
        get = _record_getter(row)
        x, p = get(lfc_key), get(padj_key)
        if x is None or p is None:
            continue
        lfc.append(float(x))
        padj.append(float(p))
        labels.append(str(get(label_key) or f"G{len(labels)}"))
    return lfc, padj, labels


def _prepare(
    lfc: Sequence[float],
    padj: Sequence[float],
    *,
    labels: Sequence[str] | None,
    padj_cutoff: float,
    lfc_cutoff: float,
) -> _Prepared:
    """校验长度 → 转 numpy → 算 ``-log10(padj)`` → 分类 → 统计。

    这是静态图与交互图的**共同上游**：两条通道拿到的 ``cats``/``stats`` 是同一份，
    不存在"图例写 123、stats 写 124"这种漂移。
    """
    import numpy as np

    if padj_cutoff <= 0:
        raise ValueError(f"padj_cutoff 必须为正数，当前为 {padj_cutoff!r}")
    if lfc_cutoff < 0:
        raise ValueError(f"lfc_cutoff 不能为负数，当前为 {lfc_cutoff!r}")
    if len(lfc) != len(padj):
        raise ValueError(f"lfc/padj 长度不一致：{len(lfc)} != {len(padj)}")
    # ⚠️ 用 len() 判空，不能写 `if not lfc`：入参可能是 numpy 数组，
    #    对多元素数组取布尔值会抛 "truth value is ambiguous"。
    if len(lfc) == 0:
        raise ValueError("数据为空，无法绘制火山图")

    x = np.asarray(lfc, dtype=float)
    padj_arr = np.asarray(padj, dtype=float)
    if not np.all(np.isfinite(x)):
        raise ValueError("lfc 含 NaN/Inf，请先过滤")
    if not np.all(np.isfinite(padj_arr)):
        raise ValueError("padj 含 NaN/Inf，请先过滤")
    if np.any(padj_arr < 0) or np.any(padj_arr > 1):
        raise ValueError("padj 必须落在 [0, 1]")

    # 防止 padj=0 时 log10 爆炸
    y = -np.log10(np.clip(padj_arr, 1e-300, None))

    cats = np.array([
        classify(float(a), float(b), padj_cutoff=padj_cutoff, lfc_cutoff=lfc_cutoff)
        for a, b in zip(x, padj_arr, strict=True)
    ])

    # 未给 labels 时用 "#<下标>" 占位 —— 保证悬停/点击始终有个可读的标识，
    # 又不会伪造看起来像真基因名的字符串（那是更坏的选择）。
    if labels is None:
        symbols = [f"#{i}" for i in range(len(x))]
    else:
        if len(labels) != len(x):
            raise ValueError(f"labels 长度必须与数据一致：{len(labels)} != {len(x)}")
        symbols = [str(s) for s in labels]

    stats: VolcanoStats = {
        "up": int((cats == "up").sum()),
        "down": int((cats == "down").sum()),
        "ns": int((cats == "ns").sum()),
        "total": int(len(cats)),
    }
    return _Prepared(x, y, padj_arr, cats, symbols, stats)


def _tex_to_plain(text: str) -> str:
    """把 matplotlib mathtext 降级为 plotly 能直接显示的纯文本。"""
    out = text
    for pattern, repl in _TEX_SUBS:
        out = re.sub(pattern, repl, out)
    return out.strip()


def _rgba(color: str, alpha: float) -> str:
    """``#RRGGBB`` + alpha → ``rgba(r,g,b,a)``；其它写法原样返回。"""
    if not isinstance(color, str) or not color.startswith("#"):
        return color
    hexpart = color.lstrip("#")
    if len(hexpart) in (3, 4):
        hexpart = "".join(ch * 2 for ch in hexpart[:3])
    elif len(hexpart) in (6, 8):
        hexpart = hexpart[:6]
    else:
        return color
    try:
        r, g, b = (int(hexpart[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return color
    return f"rgba({r},{g},{b},{alpha:g})"


def _marker_px(size_pt2: float) -> float:
    """把 matplotlib 的 ``s``（pt²，面积）近似换算成 plotly 的直径（px）。

    matplotlib 的 ``s`` 是面积，plotly 的 ``marker.size`` 是直径：直径(pt) =
    ``sqrt(s)``，再按 96dpi 换算 ``1pt = 4/3 px``。
    """
    return max(4.0, (max(size_pt2, 1e-6) ** 0.5) * 4.0 / 3.0)


# ═══════════════════════════════════════════════════════════════════════════
#  静态通道：matplotlib（+ 可选 mplcursors 悬停）
# ═══════════════════════════════════════════════════════════════════════════
def _nearest_index(xs: Any, ys: Any, tx: float, ty: float) -> int:
    """在 ``(xs, ys)`` 里找离 ``(tx, ty)`` 最近的点。

    刻意不用 ``mplcursors`` 的 ``sel.index`` —— 它的语义在"多 artist + 是
    Collection"时容易踩坑（索引落在 artist 列表上还是 collection 偏移上）；
    用坐标反查最稳，代价是 O(n) 且只在悬停时发生。
    """
    import numpy as np

    d2 = (xs - tx) ** 2 + (ys - ty) ** 2
    return int(np.argmin(d2))


def _attach_hover(
    artists: list[tuple[str, Any]],
    prepared: _Prepared,
    cfg: VizConfig,
) -> list[Any]:
    """给每个分类的散点集合挂上悬停标签（gene / log2FC / padj / -log10(padj)）。

    缺 ``mplcursors`` 时告警返回空列表，不影响出图。
    """
    try:
        import mplcursors
    except ImportError:  # pragma: no cover - 取决于运行环境
        warnings.warn(MPLCURSORS_MISSING_MSG, RuntimeWarning, stacklevel=3)
        return []

    cursors: list[Any] = []
    for cat, collection in artists:
        if collection is None:
            continue
        mask = prepared.cats == cat
        cx = prepared.x[mask]
        cy = prepared.y[mask]
        cpadj = prepared.padj[mask]
        csym = [s for s, keep in zip(prepared.symbols, mask, strict=True) if bool(keep)]
        cat_label = _CATEGORY_LABEL[cat]

        cursor = mplcursors.cursor(collection, hover=True)

        @cursor.connect("add")
        def _on_add(sel: Any, _cx: Any = cx, _cy: Any = cy,
                    _cp: Any = cpadj, _cs: list[str] = csym,
                    _lab: str = cat_label) -> None:
            i = _nearest_index(_cx, _cy, sel.target[0], sel.target[1])
            sel.annotation.set_text(
                f"{_cs[i]}\nlog2FC = {_cx[i]:.3f}\n"
                f"padj = {_cp[i]:.3e}\n-log10(padj) = {_cy[i]:.2f}"
            )
            sel.annotation.set_fontsize(cfg.tick_font_size)
            sel.annotation.set_color(cfg.text_color)
            # ⚠️ mplcursors 默认 annotation 的 ``bbox`` 是 None，
            #    ``get_bbox_patch()`` 会返回 None —— 必须用 ``set_bbox()`` 先把
            #    补丁建出来，不能直接往 ``get_bbox_patch()`` 的返回值上写属性。
            sel.annotation.set_bbox({
                "boxstyle": "round,pad=0.4",
                "facecolor": cfg.background_color,
                "edgecolor": cfg.text_color,
                "alpha": 0.9,
            })
            sel.annotation.set_text(f"[{_lab}]\n" + sel.annotation.get_text())

        cursors.append(cursor)
    return cursors


def _draw(
    lfc: Sequence[float],
    padj: Sequence[float],
    *,
    labels: Sequence[str] | None,
    padj_cutoff: float,
    lfc_cutoff: float,
    top_n: int,
    title: str,
    xlabel: str,
    ylabel: str,
    config: VizConfig | None,
    ax: Any,
    save_path: str | os.PathLike[str] | None,
    hover: bool,
) -> tuple[matplotlib.figure.Figure, _Prepared, list[Any]]:
    """两个通道共用的绘制实现，返回 ``(fig, prepared, cursors)``。"""
    import numpy as np

    # ── 唯一的配置入口：这一行取代了改造前散落各处的 10+ 个字面量 ──
    cfg = config or get_config()
    apply_matplotlib_rcparams(cfg)

    prepared = _prepare(lfc, padj, labels=labels,
                        padj_cutoff=padj_cutoff, lfc_cutoff=lfc_cutoff)
    x, y, cats = prepared.x, prepared.y, prepared.cats
    stats = prepared.stats

    if ax is None:
        from viz.config import new_axes
        fig, ax = new_axes(cfg)
    else:
        fig = ax.figure

    # 分组着色：颜色一律来自 cfg（改造前每处都写死一个 hex）
    artists: list[tuple[str, Any]] = []
    for cat in _CATEGORY_ORDER:
        mask = cats == cat
        if not mask.any():
            artists.append((cat, None))
            continue
        # ⭐ 图例说明：这里把"上调/下调/不显著"的数量直接写进图例名，
        #    与返回值里的 stats 同源（同一份 mask），不会互相打架。
        collection = ax.scatter(
            x[mask], y[mask],
            s=cfg.marker_size if cat != "ns" else cfg.marker_size * 0.6,
            c=cfg.color_for_category(cat),
            alpha=cfg.scatter_alpha if cat != "ns" else cfg.scatter_alpha * 0.6,
            edgecolors="none",
            label=f"{_CATEGORY_LABEL[cat]} ({stats[cat]})",  # type: ignore[literal-required]
            zorder=3 if cat != "ns" else 2,
        )
        artists.append((cat, collection))

    # 阈值辅助线
    sig_y = -np.log10(padj_cutoff)
    ax.axhline(sig_y, color=cfg.threshold_color, linestyle=cfg.grid_linestyle,
               linewidth=cfg.line_width * 0.8, alpha=0.9)
    ax.axvline(lfc_cutoff, color=cfg.threshold_color, linestyle=cfg.grid_linestyle,
               linewidth=cfg.line_width * 0.8, alpha=0.9)
    ax.axvline(-lfc_cutoff, color=cfg.threshold_color, linestyle=cfg.grid_linestyle,
               linewidth=cfg.line_width * 0.8, alpha=0.9)

    # Top-N 标注
    if top_n > 0:
        labelled = [(i, float(y[i])) for i in range(len(x)) if cats[i] != "ns"]
        labelled.sort(key=lambda item: item[1], reverse=True)
        for idx, yi in labelled[:top_n]:
            ax.annotate(
                prepared.symbols[idx], (x[idx], yi),
                textcoords="offset points", xytext=(4, 4),
                fontsize=cfg.tick_font_size, color=cfg.text_color,
            )

    # 标题/标签/网格：字号与透明度均取自 cfg（改造前逐个写死）
    ax.set_title(title, fontsize=cfg.title_font_size, color=cfg.text_color)
    ax.set_xlabel(xlabel, fontsize=cfg.label_font_size)
    ax.set_ylabel(ylabel, fontsize=cfg.label_font_size)
    ax.grid(True, alpha=cfg.grid_alpha, color=cfg.grid_color,
            linestyle=cfg.grid_linestyle, linewidth=0.5)
    ax.legend(fontsize=cfg.legend_font_size, loc="upper right")

    cursors = _attach_hover(artists, prepared, cfg) if hover else []

    fig.tight_layout()
    if save_path is not None:
        save_figure(fig, save_path, cfg=cfg)
    return fig, prepared, cursors


def volcano_figure(
    lfc: Sequence[float],
    padj: Sequence[float],
    *,
    labels: Sequence[str] | None = None,
    padj_cutoff: float = DEFAULT_PADJ_CUTOFF,
    lfc_cutoff: float = DEFAULT_LFC_CUTOFF,
    top_n: int = 0,
    title: str = "Volcano Plot",
    xlabel: str = r"$\log_2$ Fold Change",
    ylabel: str = r"$-\log_{10}(\mathrm{padj})$",
    config: VizConfig | None = None,
    ax: Any = None,
    save_path: str | os.PathLike[str] | None = None,
    hover: bool = False,
) -> matplotlib.figure.Figure:
    """绘制**静态**火山图并返回 ``Figure``（绘图原语）。

    这是改造前 ``plot_volcano`` 的原行为，保留下来是因为"拿到 Figure"仍然是刚需：
    组合子图、二次加工、嵌进 PPT 导出链路、复用外部 ``ax``。

    Parameters
    ----------
    lfc, padj:
        等长的 log2FC 与校正 p 值序列。
    labels:
        基因名；给了且 ``top_n > 0`` 时标注最显著的 Top-N，并作为悬停标签。
    top_n:
        标注数量（0 = 不标注）。
    hover:
        ``True`` 时挂 ``mplcursors`` 悬停标签（显示 gene / log2FC / padj）。
        仅在交互式后端（窗口、notebook）里可见；存成 PNG 时不会有标签。
    config:
        传入则用这份配置；为 ``None`` 时用全局单例 :func:`get_config`。
    ax:
        复用已有坐标轴（``None`` 则按配置新建 ``(fig, ax)``）。
    save_path:
        给了就按配置 DPI/底色/裁剪保存。
    """
    fig, _prepared, _cursors = _draw(
        lfc, padj, labels=labels, padj_cutoff=padj_cutoff, lfc_cutoff=lfc_cutoff,
        top_n=top_n, title=title, xlabel=xlabel, ylabel=ylabel,
        config=config, ax=ax, save_path=save_path, hover=hover,
    )
    return fig


def _figure_to_base64(fig: matplotlib.figure.Figure, cfg: VizConfig,
                      fmt: str = "png") -> str:
    """把 Figure 渲染成内存图片并返回**裸** base64（无 data-uri 前缀）。"""
    buf = io.BytesIO()
    kwargs: dict[str, Any] = {"format": fmt, "dpi": cfg.dpi,
                              "facecolor": cfg.background_color}
    if cfg.bbox_inches is not None:
        kwargs["bbox_inches"] = cfg.bbox_inches
    fig.savefig(buf, **kwargs)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ═══════════════════════════════════════════════════════════════════════════
#  交互通道：plotly HTML
# ═══════════════════════════════════════════════════════════════════════════
#: 点击高亮的客户端脚本（注入 ``to_html(post_script=...)``）。
#: 用 ``__DIV_ID__`` 占位再 replace —— 脚本里全是 JS 花括号，f-string 会把它们当格式字段。
_CLICK_HIGHLIGHT_JS = """
(function () {
  var gd = document.getElementById("__DIV_ID__");
  if (!gd || typeof Plotly === "undefined") { return; }
  var HIGHLIGHT = "__highlight__";
  var current = null;

  function highlightTraceIndex() {
    for (var i = 0; i < gd.data.length; i++) {
      if (gd.data[i].name === HIGHLIGHT) { return i; }
    }
    return -1;
  }

  function clearSelection() {
    var idx = highlightTraceIndex();
    if (idx >= 0) { Plotly.deleteTraces(gd, idx); }
    Plotly.relayout(gd, { annotations: [] });
    current = null;
  }

  function symbolOf(point) {
    var txt = point.data && point.data.text;
    if (!txt) { return ""; }
    return (typeof txt === "string") ? txt : (txt[point.pointNumber] || "");
  }

  function padjOf(point) {
    var cd = point.data && point.data.customdata;
    if (!cd) { return null; }
    var row = cd[point.pointNumber];
    return (row && row.length) ? row[0] : null;
  }

  gd.on("plotly_click", function (ev) {
    var point = ev.points[0];
    if (point.data && point.data.name === HIGHLIGHT) { return; }

    var key = point.curveNumber + ":" + point.pointNumber;
    if (current === key) { clearSelection(); return; }   // 再次点击同一点 = 取消高亮
    current = key;

    var idx = highlightTraceIndex();
    var ring = {
      name: HIGHLIGHT,
      type: "scatter",
      mode: "markers",
      x: [point.x],
      y: [point.y],
      marker: { size: 18, color: "rgba(0,0,0,0)", line: { color: "#111111", width: 3 } },
      hoverinfo: "skip",
      showlegend: false
    };
    if (idx < 0) { Plotly.addTraces(gd, [ring]); }
    else { Plotly.restyle(gd, { x: [[point.x]], y: [[point.y]] }, [idx]); }

    var symbol = symbolOf(point);
    var padj = padjOf(point);
    var label = symbol + "<br>log2FC = " + Number(point.x).toFixed(3);
    if (padj !== null) { label += "<br>padj = " + Number(padj).toExponential(2); }

    Plotly.relayout(gd, {
      annotations: [{
        x: point.x, y: point.y, xref: "x", yref: "y",
        text: label, showarrow: true, arrowhead: 2, ax: 28, ay: -28,
        align: "left",
        font: { size: 13, color: "#111111" },
        bgcolor: "rgba(255,255,255,0.88)",
        bordercolor: "#111111", borderwidth: 1
      }]
    });
  });
})();
"""


def _build_plotly_html(
    prepared: _Prepared,
    *,
    padj_cutoff: float,
    lfc_cutoff: float,
    top_n: int,
    title: str,
    xlabel: str,
    ylabel: str,
    cfg: VizConfig,
    include_plotlyjs: str | bool,
    div_id: str,
) -> str:
    """构造交互火山图的**自包含** HTML 字符串。

    三条 trace（up/down/ns）各自带 ``customdata``（padj 原值）与 ``text``（基因名），
    悬停模板即从这三处取数 —— 所以悬停能看到 gene symbol + log2FC + p-value。
    """
    import plotly.graph_objects as go

    fig = go.Figure()

    for cat in _CATEGORY_ORDER:
        mask = prepared.cats == cat
        n = int(mask.sum())
        if n == 0:
            continue
        ns = cat == "ns"
        fig.add_trace(go.Scatter(
            x=prepared.x[mask],
            y=prepared.y[mask],
            mode="markers",
            name=f"{_CATEGORY_LABEL[cat]} ({n})",
            text=[s for s, keep in zip(prepared.symbols, mask, strict=True) if bool(keep)],
            customdata=[[float(p)] for p in prepared.padj[mask]],
            hovertemplate=(
                "<b>%{text}</b><br>"
                "log2FC = %{x:.3f}<br>"
                "p-value (padj) = %{customdata[0]:.3e}<br>"
                "-log10(padj) = %{y:.2f}"
                f"<extra>{_CATEGORY_LABEL[cat]}</extra>"
            ),
            marker={
                "color": cfg.color_for_category(cat),
                "size": _marker_px(cfg.marker_size * (0.6 if ns else 1.0)),
                "opacity": cfg.scatter_alpha * (0.6 if ns else 1.0),
                "line": {"width": 0},
            },
        ))

    # 阈值辅助线（用 layout shape，跟着缩放一起动）
    sig_y = -math.log10(padj_cutoff)
    dash = "dash" if cfg.grid_linestyle in ("--", "-.", ":") else "solid"
    line = {"color": _rgba(cfg.threshold_color, 0.9), "width": max(cfg.line_width, 1.0),
            "dash": dash}
    fig.add_shape(type="line", xref="paper", x0=0, x1=1, y0=sig_y, y1=sig_y, line=line)
    fig.add_shape(type="line", xref="x", yref="paper", x0=lfc_cutoff, x1=lfc_cutoff,
                  y0=0, y1=1, line=line)
    fig.add_shape(type="line", xref="x", yref="paper", x0=-lfc_cutoff, x1=-lfc_cutoff,
                  y0=0, y1=1, line=line)

    # Top-N 基因名直接标在图上（与静态通道同一套选择规则）
    annotations: list[dict[str, Any]] = []
    if top_n > 0:
        order = sorted(
            (i for i in range(len(prepared.x)) if prepared.cats[i] != "ns"),
            key=lambda i: float(prepared.y[i]), reverse=True,
        )[:top_n]
        annotations = [{
            "x": float(prepared.x[i]), "y": float(prepared.y[i]),
            "text": prepared.symbols[i], "showarrow": False,
            "xanchor": "left", "yanchor": "bottom",
            "font": {"size": cfg.tick_font_size, "color": cfg.text_color},
        } for i in order]

    fig.update_layout(
        title={"text": title, "font": {"size": cfg.title_font_size, "color": cfg.text_color}},
        xaxis_title=_tex_to_plain(xlabel),
        yaxis_title=_tex_to_plain(ylabel),
        # 尺寸/DPI：plotly 用 px，按 figure_size(inch) × 100px/in 折算
        width=int(cfg.figure_size[0] * 100),
        height=int(cfg.figure_size[1] * 100),
        font={"family": cfg.font_family, "size": cfg.font_size, "color": cfg.text_color},
        plot_bgcolor=cfg.background_color,
        paper_bgcolor=cfg.background_color,
        # ⭐ 框选放大：dragmode="zoom" 时拖拽即"框选放大"（双击/模式栏可切回）
        dragmode="zoom",
        hovermode="closest",
        legend={"font": {"size": cfg.legend_font_size, "color": cfg.text_color}},
        annotations=annotations,
    )
    fig.update_xaxes(
        gridcolor=_rgba(cfg.grid_color, cfg.grid_alpha),
        zerolinecolor=_rgba(cfg.grid_color, cfg.grid_alpha),
    )
    fig.update_yaxes(
        gridcolor=_rgba(cfg.grid_color, cfg.grid_alpha),
        zerolinecolor=_rgba(cfg.grid_color, cfg.grid_alpha),
    )

    return fig.to_html(
        full_html=True,               # 输出完整 HTML 文档而非片段
        include_plotlyjs=include_plotlyjs,
        div_id=div_id,
        config={
            "scrollZoom": True,       # 滚轮缩放
            "displaylogo": False,
            "responsive": True,
            # 框选 / 套索按钮：配合 dragmode 做"框选放大"
            "modeBarButtonsToAdd": ["select2d", "lasso2d"],
        },
        # ⭐ 点击高亮某个基因：注入 plotly_click 回调
        post_script=_CLICK_HIGHLIGHT_JS.replace("__DIV_ID__", div_id),
    )


# ═══════════════════════════════════════════════════════════════════════════
#  对外主入口：返回 dict
# ═══════════════════════════════════════════════════════════════════════════
_DIV_COUNTER = [0]


def _next_div_id() -> str:
    """生成默认 div id（进程内递增，保证同页多图不撞 id）。"""
    _DIV_COUNTER[0] += 1
    return f"volcano-plot-{_DIV_COUNTER[0]}"


def plot_volcano(
    lfc: Sequence[float],
    padj: Sequence[float],
    *,
    labels: Sequence[str] | None = None,
    padj_cutoff: float = DEFAULT_PADJ_CUTOFF,
    lfc_cutoff: float = DEFAULT_LFC_CUTOFF,
    top_n: int = 0,
    title: str = "Volcano Plot",
    xlabel: str = r"$\log_2$ Fold Change",
    ylabel: str = r"$-\log_{10}(\mathrm{padj})$",
    config: VizConfig | None = None,
    ax: Any = None,
    save_path: str | os.PathLike[str] | None = None,
    hover: bool = False,
    interactive: bool = False,
    include_plotlyjs: str | bool = "cdn",
    div_id: str | None = None,
) -> VolcanoResult:
    """渲染火山图，返回 ``{image_base64, interactive_html?, stats}``。

    Parameters
    ----------
    interactive:
        ``False``（默认）只出**静态图片**（matplotlib → base64 PNG），行为与改造前
        "出一张静态图"一致；``True`` 时额外把同一份数据交给 plotly 渲染成自包含
        HTML，此时结果里多一个 ``interactive_html`` 键。
    hover:
        matplotlib 侧是否挂 ``mplcursors`` 悬停标签（需要交互式后端才看得见，
        对 base64/PNG 输出无可见影响；缺依赖时告警降级）。
    include_plotlyjs:
        交互 HTML 的内嵌方式。``"cdn"``（默认，HTML 仅几十 KB，需要联网）、
        ``True``（把 3MB+ 的 plotly.js 内联进来，**完全离线可用**）、
        ``"directory"``（引用同目录的 ``plotly.min.js``）。
    div_id:
        交互图的 DOM id，默认自动生成；同一页面嵌多张图时建议显式指定。
    ax:
        复用已有坐标轴。此时 Figure 归调用方所有，本函数不会关闭它。
    save_path:
        额外把静态 PNG 落盘（按配置 DPI/底色）。

    Returns
    -------
    VolcanoResult
        ``{"image_base64": str, "stats": {...}}``，``interactive=True`` 且 plotly
        可用时另含 ``"interactive_html": str``。

    Notes
    -----
    未提供 ``labels`` 时，基因名用 ``"#<下标>"`` 占位，以保证悬停/点击有可读标识。
    """
    cfg = config or get_config()
    owns_figure = ax is None

    fig, prepared, _cursors = _draw(
        lfc, padj, labels=labels, padj_cutoff=padj_cutoff, lfc_cutoff=lfc_cutoff,
        top_n=top_n, title=title, xlabel=xlabel, ylabel=ylabel,
        config=cfg, ax=ax, save_path=save_path, hover=hover,
    )

    result: VolcanoResult = {
        "image_base64": _figure_to_base64(fig, cfg),
        "stats": prepared.stats,
    }

    # 我们自己造的 Figure 就由我们收尾：Web 服务里每请求一张图，
    # 不关掉就是稳定的内存泄漏（matplotlib 会一直持有 pyplot 全局引用）。
    if owns_figure:
        import matplotlib.pyplot as plt
        plt.close(fig)

    if interactive:
        try:
            import plotly.graph_objects  # noqa: F401  仅探测可用性
        except ImportError:
            # 与 viz.config 的既有约定一致：可选增强项缺失 -> 告警 + 降级，
            # 不因为一个交互开关把整条出图链路炸掉。
            warnings.warn(PLOTLY_MISSING_MSG, RuntimeWarning, stacklevel=2)
        else:
            result["interactive_html"] = _build_plotly_html(
                prepared,
                padj_cutoff=padj_cutoff, lfc_cutoff=lfc_cutoff, top_n=top_n,
                title=title, xlabel=xlabel, ylabel=ylabel, cfg=cfg,
                include_plotlyjs=include_plotlyjs,
                div_id=div_id or _next_div_id(),
            )

    return result


if __name__ == "__main__":  # pragma: no cover - 手动演示
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np

    rng = np.random.default_rng(42)
    n = 3000
    demo_lfc = rng.normal(0, 1.2, n)
    demo_padj = np.clip(rng.beta(0.4, 6.0, n), 1e-8, 1.0)
    demo_labels = [f"GENE{i}" for i in range(n)]

    here = Path(__file__).resolve().parent

    static = plot_volcano(demo_lfc, demo_padj, labels=demo_labels, top_n=12,
                          title="Volcano Plot (demo)",
                          save_path=here / "_demo_volcano.png")
    print("static :", len(static["image_base64"]), "b64 chars; stats =", static["stats"])

    both = plot_volcano(demo_lfc, demo_padj, labels=demo_labels, top_n=12,
                        title="Volcano Plot (interactive demo)", interactive=True,
                        include_plotlyjs=True)  # 内联 plotly.js，离线可开
    html = both.get("interactive_html")
    if html is None:
        print("interactive: 未生成（缺 plotly）")
    else:
        out = here / "_demo_volcano.html"
        with out.open("w", encoding="utf-8") as fh:
            fh.write(html)
        print("interactive:", len(html), "chars ->", out)

"""tests/test_viz_volcano.py — 火山图「静态 + 交互」双通道回归。

覆盖 :mod:`viz.volcano` 的对外契约：

* ``plot_volcano`` 返回 ``{image_base64, stats[, interactive_html]}``；
* ``stats`` 与手算一致，且**两个通道共用同一份分类结果**（不会各算一套）；
* 静态通道产出合法 PNG，且尺寸真由 ``VizConfig.dpi`` 驱动；
* 交互通道悬停含 gene symbol / log2FC / p-value，支持框选缩放，注入点击高亮脚本；
* 可选依赖（plotly / mplcursors）缺失时**告警降级**而非抛异常；
* 改造前 ``plot_volcano`` 的全部关键字仍被接受。

⚠️ 用 ``Agg`` 后端：CI 无显示设备，且我们不希望交互式窗口被真的弹出来。
⚠️ 需要 plotly / mplcursors 的用例一律先 ``importorskip``，装不上不应让整仓变红。
"""

from __future__ import annotations

import base64
import re
import sys
import warnings

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from viz import volcano as vol
from viz.config import VizConfig, config_override

#: 构造已知答案的数据集：(log2FC, padj, symbol) → up=2 / down=1 / ns=3
ROWS = [
    (2.5, 0.001, "TP53"),      # up
    (1.8, 0.010, "BRCA1"),     # up
    (-2.0, 0.002, "MYC"),      # down
    (0.1, 0.900, "ACTB"),      # ns（lfc 不够）
    (0.4, 0.030, "GAPDH"),     # ns（lfc 不够）
    (-0.2, 0.800, "TUBB"),     # ns
]
LFC = [r[0] for r in ROWS]
PADJ = [r[1] for r in ROWS]
SYM = [r[2] for r in ROWS]
EXPECT_STATS = {"up": 2, "down": 1, "ns": 3, "total": 6}


@pytest.fixture()
def _close_figures():
    """每个用例后关掉所有 pyplot figure，避免测试间互相污染 / 泄漏。"""
    yield
    plt.close("all")


def _png_size(raw: bytes) -> tuple[int, int]:
    """从 PNG 字节流里读 IHDR 的宽高（不引入 Pillow）。"""
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "不是合法 PNG"
    assert raw[12:16] == b"IHDR"
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


# ═══════════════════════════════════════════════════════════════════════════
#  静态通道
# ═══════════════════════════════════════════════════════════════════════════
def test_stats_match_manual_counts():
    result = vol.plot_volcano(LFC, PADJ, labels=SYM)
    assert result["stats"] == EXPECT_STATS
    # 键序也是契约的一部分：前端按固定顺序渲染
    assert list(result["stats"]) == ["up", "down", "ns", "total"]


def test_interactive_false_returns_static_only():
    result = vol.plot_volcano(LFC, PADJ, labels=SYM)
    assert set(result) == {"image_base64", "stats"}
    assert "interactive_html" not in result


def test_static_image_is_decodable_png():
    result = vol.plot_volcano(LFC, PADJ, labels=SYM)
    raw = base64.b64decode(result["image_base64"])
    width, height = _png_size(raw)
    assert width > 0 and height > 0
    # 裸 base64，前端自行拼 data-uri 前缀
    assert not result["image_base64"].startswith("data:")


def test_dpi_config_drives_png_pixels(_close_figures):
    """``dpi`` 必须真的改变输出像素，而不只是改个配置值。

    ``bbox_inches=None``（不裁剪）时，PNG 尺寸 == ``figure_size × dpi``，
    这是个可精确断言的恒等式。
    """
    with config_override(VizConfig(dpi=100, bbox_inches=None)):
        r100 = vol.plot_volcano(LFC, PADJ)
    with config_override(VizConfig(dpi=50, bbox_inches=None)):
        r50 = vol.plot_volcano(LFC, PADJ)
    assert _png_size(base64.b64decode(r100["image_base64"])) == (1000, 800)
    assert _png_size(base64.b64decode(r50["image_base64"])) == (500, 400)


def test_volcano_figure_is_still_a_figure_primitve(_close_figures):
    fig = vol.volcano_figure(LFC, PADJ, labels=SYM, top_n=2)
    assert isinstance(fig, matplotlib.figure.Figure)
    ax = fig.axes[0]
    legend = sorted(t.get_text() for t in ax.get_legend().get_texts())
    # ⭐ 图例说明：三类数量统计
    assert legend == sorted(["Up-regulated (2)", "Down-regulated (1)", "Not Significant (3)"])
    # Top-N 标注取最显著的非 ns 点：up 里 TP53(3.0) 最高，down 里 MYC(2.7)
    assert {t.get_text() for t in ax.texts} == {"TP53", "MYC"}


def test_plot_volcano_does_not_leak_figures(_close_figures):
    before = set(plt.get_fignums())
    vol.plot_volcano(LFC, PADJ, labels=SYM)
    assert set(plt.get_fignums()) == before


def test_caller_supplied_axes_is_not_closed(_close_figures):
    fig = plt.figure()
    ax = fig.add_subplot(111)
    vol.plot_volcano(LFC, PADJ, ax=ax)
    assert ax.figure is fig
    assert fig.number in plt.get_fignums()


# ═══════════════════════════════════════════════════════════════════════════
#  交互通道
# ═══════════════════════════════════════════════════════════════════════════
def test_interactive_html_has_all_requested_features():
    pytest.importorskip("plotly")
    result = vol.plot_volcano(LFC, PADJ, labels=SYM, top_n=2, interactive=True,
                              include_plotlyjs="cdn", div_id="vtest")
    html = result["interactive_html"]

    assert html.lstrip().lower().startswith("<!doctype html")
    assert 'id="vtest"' in html

    # 悬停：gene symbol + log2FC + p-value
    assert "hovertemplate" in html and "%{text}" in html
    assert "log2FC = %{x:.3f}" in html
    assert "p-value (padj) = %{customdata[0]:.3e}" in html
    assert all(s in html for s in ("TP53", "BRCA1", "MYC"))

    # 框选放大
    assert re.search(r'"dragmode":\s*"zoom"', html)
    assert "scrollZoom" in html and "select2d" in html and "lasso2d" in html

    # 点击高亮
    assert "plotly_click" in html and "__highlight__" in html

    # 配置驱动：三色 + 轴标题去 mathtext（plotly 不解析 mathtext）
    assert all(c in html for c in ("#D73027", "#1A9850", "#AAAAAA"))
    assert "log2 Fold Change" in html and "-log10(padj)" in html and "\\log" not in html


def test_both_channels_share_one_pair_of_stats():
    """交互图与静态图的 stats 必须同源，否则前端显示的数字会与图上不一致。"""
    pytest.importorskip("plotly")
    static = vol.plot_volcano(LFC, PADJ, labels=SYM)
    both = vol.plot_volcano(LFC, PADJ, labels=SYM, interactive=True,
                            include_plotlyjs="cdn")
    assert both["stats"] == static["stats"]


def test_interactive_html_follows_config(_close_figures):
    pytest.importorskip("plotly")
    with config_override(VizConfig(color_up="#B2182B", figure_size=(6, 4),
                                   font_family="Times New Roman")):
        html = vol.plot_volcano(LFC, PADJ, interactive=True,
                                include_plotlyjs="cdn")["interactive_html"]
    assert "#B2182B" in html and "#D73027" not in html
    assert re.search(r'"width":\s*600', html) and re.search(r'"height":\s*400', html)
    assert "Times New Roman" in html


def test_plotlyjs_embedding_modes():
    pytest.importorskip("plotly")
    cdn = vol.plot_volcano(LFC, PADJ, interactive=True,
                           include_plotlyjs="cdn")["interactive_html"]
    inline = vol.plot_volcano(LFC, PADJ, interactive=True,
                              include_plotlyjs=True)["interactive_html"]
    assert len(cdn) < 100_000            # 走 CDN：HTML 本体很小
    assert len(inline) > 1_000_000       # 内联 plotly.js：离线可用


def test_default_div_ids_are_unique():
    pytest.importorskip("plotly")
    a = vol.plot_volcano(LFC, PADJ, interactive=True,
                         include_plotlyjs="cdn")["interactive_html"]
    b = vol.plot_volcano(LFC, PADJ, interactive=True,
                         include_plotlyjs="cdn")["interactive_html"]
    id_a = re.search(r'id="(volcano-plot-\d+)"', a)
    id_b = re.search(r'id="(volcano-plot-\d+)"', b)
    assert id_a and id_b and id_a.group(1) != id_b.group(1)


# ═══════════════════════════════════════════════════════════════════════════
#  可选依赖降级（沿用 viz.config「告警而非抛异常」的约定）
# ═══════════════════════════════════════════════════════════════════════════
def test_missing_plotly_degrades_with_warning(monkeypatch):
    monkeypatch.setitem(sys.modules, "plotly", None)
    monkeypatch.setitem(sys.modules, "plotly.graph_objects", None)
    with pytest.warns(RuntimeWarning, match="plotly"):
        result = vol.plot_volcano(LFC, PADJ, labels=SYM, interactive=True)
    assert "interactive_html" not in result
    assert result["stats"] == EXPECT_STATS          # 静态结果不受影响
    assert len(result["image_base64"]) > 100


def test_missing_mplcursors_degrades_with_warning(monkeypatch, _close_figures):
    monkeypatch.setitem(sys.modules, "mplcursors", None)
    with pytest.warns(RuntimeWarning, match="mplcursors"):
        fig = vol.volcano_figure(LFC, PADJ, hover=True)
    assert len(fig.axes) == 1                       # 图照出，只是没有悬停


def test_hover_annotation_content(_close_figures):
    """真触发一次 mplcursors 的 ``add`` 回调，校验悬停文案。

    ⚠️ ``Cursor`` 不是 ``CallbackRegistry`` 子类，回调放在私有 ``_callbacks``
    （``{"add": [fn, ...], "remove": [...]}``）；这是测试专用访问。
    ⚠️ cursor 按 ``_CATEGORY_ORDER=("ns","up","down")`` 依次创建，所以**不能**
    假定 ``cursors[0]`` 就是 up —— 逐类各开一个真实 Annotation 来验。
    """
    import mplcursors

    made: list[object] = []
    real = mplcursors.cursor

    def spy(*args, **kwargs):
        cursor = real(*args, **kwargs)
        made.append(cursor)
        return cursor

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mplcursors, "cursor", spy)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)   # 有依赖就不该告警
            fig = vol.volcano_figure(LFC, PADJ, labels=SYM, hover=True)
    finally:
        monkeypatch.undo()

    assert len(made) == 3          # ns / up / down 各一个

    # 光标固定在 TP53 的坐标 (lfc=2.5, -log10(0.001)=3.0)
    texts: list[str] = []
    for cursor in made:
        annotation = fig.axes[0].annotate("", (0.0, 0.0))
        sel = type("Sel", (), {"target": (2.5, 3.0), "annotation": annotation})()
        for signal, callbacks in cursor._callbacks.items():
            if signal != "add":
                continue
            for fn in (list(callbacks.values()) if isinstance(callbacks, dict)
                       else list(callbacks)):
                fn(sel)
        texts.append(annotation.get_text())

    up_text = next(t for t in texts if "[Up-regulated]" in t)
    ns_text = next(t for t in texts if "[Not Significant]" in t)
    assert "TP53" in up_text
    assert "log2FC = 2.500" in up_text
    assert "padj = 1.000e-03" in up_text
    assert "-log10(padj) = 3.00" in up_text
    # 各分类只在自己的点里找最近点，不会串到别的分类
    assert "GAPDH" in ns_text and "TP53" not in ns_text


# ═══════════════════════════════════════════════════════════════════════════
#  入参校验与向后兼容
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(("lfc", "padj", "kwargs"), [
    ([1.0], [0.1, 0.2], {}),                                   # 长度不一致
    ([], [], {}),                                              # 空数据
    ([1.0], [1.5], {}),                                        # padj 越界
    ([float("nan")], [0.1], {}),                               # NaN
    ([1.0, 2.0], [0.1, 0.2], {"labels": ["A"]}),               # labels 长度不符
    ([1.0], [0.1], {"padj_cutoff": 0.0}),                      # 阈值非法
])
def test_invalid_input_raises_value_error(lfc, padj, kwargs):
    with pytest.raises(ValueError):
        vol.plot_volcano(lfc, padj, **kwargs)


def test_labels_are_optional_and_placeholder_is_readable():
    pytest.importorskip("plotly")
    # 不传 labels 也要能出图，且悬停有可读占位（"#<下标>"）而不是 None
    result = vol.plot_volcano(LFC, PADJ, interactive=True, include_plotlyjs="cdn")
    assert result["stats"] == EXPECT_STATS
    assert "#0" in result["interactive_html"]


def test_pre_refactor_kwargs_still_accepted():
    """改造只改了返回值，旧关键字必须全部仍然可用。"""
    result = vol.plot_volcano(
        LFC, PADJ, labels=SYM, padj_cutoff=0.05, lfc_cutoff=0.585, top_n=2,
        title="T", xlabel="X", ylabel="Y", ax=None, save_path=None,
    )
    assert result["stats"] == EXPECT_STATS


def test_save_path_writes_static_png(tmp_path, _close_figures):
    target = tmp_path / "volcano.png"
    vol.plot_volcano(LFC, PADJ, labels=SYM, save_path=str(target))
    assert target.exists() and target.stat().st_size > 0


def test_from_records_bridge_still_works():
    records = [{"gene_id": r[2], "log2FoldChange": r[0], "padj": r[1]} for r in ROWS]
    lfc, padj, labels = vol.volcano_from_records(records)
    assert vol.plot_volcano(lfc, padj, labels=labels)["stats"] == EXPECT_STATS


def test_classify_thresholds():
    assert vol.classify(2.0, 0.001) == "up"
    assert vol.classify(-2.0, 0.001) == "down"
    assert vol.classify(2.0, 0.5) == "ns"       # 显著但倍数不够
    assert vol.classify(0.0, 0.001) == "ns"     # 倍数不够


def test_package_exports_stay_in_sync():
    """``viz.__all__`` 与懒加载表必须一致。

    ``__all__`` 为了躲开 ruff F401 的误报（动态表达式它看不穿）而写成了字面量，
    于是有了「两份清单」的漂移风险：往 ``_LAZY_EXPORTS`` 加了名字却忘了同步
    ``__all__``，``from viz import X`` 能成功但 ``import *`` / IDE 补全会漏。
    这条用例把该不变量钉死。
    """
    import viz

    assert set(viz.__all__) == set(viz._LAZY_EXPORTS)
    assert viz.__all__ == sorted(viz.__all__)
    # 懒加载导出的名字必须真的取得到
    for name in viz.__all__:
        assert getattr(viz, name) is not None

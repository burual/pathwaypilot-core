"""viz/config.py — 可视化配置中心（单一事实来源）。

## 为什么需要它

`viz/` 下的 8 个绘图模块此前各自硬编码了 `figure_size` / `dpi` / 字体 / 三色配色 /
`grid_alpha`。这带来三个可复现的坏味道：

1. **改一处要动八处**：换整体视觉（比如论文出图把 DPI 提到 300）得逐个文件改；
2. **色值漂移**：同一个红在 A 文件是 ``#D73027``、在 B 文件是 ``#d73027``，
   肉眼一致、字节不同，diff 里永远在打架；
3. **无法按环境切换**：CI 出小图、论文出大图、高分屏出 2x 图，代码里写死了就看运气。

本模块把上述量收敛成一个**不可变**的 :class:`VizConfig`，并提供：

* :func:`get_config` —— 进程内单例，环境变量只解析一次；
* **环境变量覆盖** —— ``VIZ_DPI=200``、``VIZ_FIGURE_SIZE=12,9`` 等，
  非法值**告警并回退默认**（绝不因为一个拼错的 DPI 让整条出图链路炸掉）；
* :func:`apply_matplotlib_rcparams` —— 把配置推给 matplotlib 全局 ``rcParams``；
* :func:`new_axes` —— 按配置创建 ``(fig, ax)``，尺寸/DPI/底色一次到位；
* 语义配色 helper —— ``color_for_category("up")`` / ``category_colors()`` / ``palette_colors(n)``；
* :func:`reset_config` / :func:`config_override` —— 测试与"临时换一套风格"用。

## 用法

    from viz.config import get_config

    cfg = get_config()
    print(cfg.figure_size, cfg.dpi, cfg.color_up)   # (10.0, 8.0) 150 #D73027

    # 临时覆盖（线程内不影响他人？见 config_override 文档 —— 它是全局的，仅供串行场景）
    from viz.config import VizConfig, config_override
    with config_override(VizConfig(dpi=300)):
        ...  # 这段代码里 get_config() 会拿到 dpi=300

命令行覆盖：

    VIZ_DPI=200 VIZ_FIGURE_SIZE=12,9 python -m viz.volcano

## 与业务阈值的关系（边界说明）

本模块**只管视觉**。``padj_cutoff`` / ``lfc_cutoff`` 这类**业务/统计阈值**不属于
视觉配置，仍留在各绘图模块的函数签名里 —— 把二者混进同一个 dataclass 会让
"改配色" 和 "改显著性标准" 这两件完全不同的事走同一个开关，那是更糟的设计。
"""

from __future__ import annotations

import os
import re
import threading
import warnings
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from dataclasses import replace as _dc_replace
from typing import TYPE_CHECKING, Any, Iterator, Sequence

if TYPE_CHECKING:  # 仅供注解；matplotlib 在函数内延迟导入，保证 config 可脱离 matplotlib 使用
    import matplotlib.axes
    import matplotlib.figure

__all__ = [
    "DEFAULT_FONT_FALLBACKS",
    "DEFAULT_PALETTE",
    "ENV_PREFIX",
    "VizConfig",
    "apply_matplotlib_rcparams",
    "config_override",
    "get_config",
    "new_axes",
    "reset_config",
    "save_figure",
]

__version__ = "1.0.0"

#: 环境变量前缀：``VIZ_DPI`` / ``VIZ_COLOR_UP`` / ...
ENV_PREFIX = "VIZ_"

#: 分类色板（多组对比/降维分组用）。放在语义三色之后，避免与 up/down/ns 撞色。
DEFAULT_PALETTE: tuple[str, ...] = (
    "#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3",
    "#937860", "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD",
)

#: 字体回退链。``font_family`` 排最前，其后是跨平台 + CJK 兜底
#: （无中文字体时，中文标签会退化成"豆腐块"，这条链是防线）。
DEFAULT_FONT_FALLBACKS: tuple[str, ...] = (
    "DejaVu Sans",
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "PingFang SC",
    "Arial Unicode MS",
)

#: "上调" 的写法别名（大小写无关）。中文别名让下游可直接传 "上调"。
_UP_ALIASES = frozenset({"up", "up-regulated", "upregulated", "up_regulated", "上调"})
#: "下调" 的写法别名。
_DOWN_ALIASES = frozenset({"down", "down-regulated", "downregulated", "down_regulated", "下调"})

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB_RE = re.compile(r"^rgba?\([^)]*\)$", re.IGNORECASE)
_NAMED_RE = re.compile(r"^[A-Za-z]+$")

_DARK_BACKGROUND = "#1E1E1E"
_DARK_TEXT = "#E8E8E8"
_DARK_GRID = "#4A4A4A"


def _is_color(value: object) -> bool:
    """判断是否像合法颜色：``#RGB[A]`` / ``#RRGGBB[AA]`` / ``rgb[a](...)`` / 命名色。"""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if text.startswith("#"):
        return bool(_HEX_RE.match(text))
    if _RGB_RE.match(text):
        return True
    return bool(_NAMED_RE.match(text))


@dataclass(frozen=True, slots=True)
class VizConfig:
    """全局可视化配置。``frozen=True`` 防止单例被调用方就地改坏。

    需要变体时用 :meth:`with_overrides`（返回**新**实例）或 :func:`config_override`。

    字段分两组：前 10 个是**核心视觉参数**（题目要求）；其后是**扩展字段**，
    由 8 个绘图模块共用（字号梯度、线宽、色板、输出格式等）。
    """

    # ----------------------------- 核心字段 ----------------------------- #
    #: 画布尺寸（英寸），(宽, 高)。
    figure_size: tuple[float, float] = (10.0, 8.0)
    #: 出图 DPI（屏幕 100~150，论文 300+）。
    dpi: int = 150
    #: 主字体族。
    font_family: str = "Arial"
    #: 正文字号（pt）。
    font_size: int = 12
    #: 标题字号（pt）。
    title_font_size: int = 16
    #: 上调色 —— 红（中国惯例：涨/上调为红）。
    color_up: str = "#D73027"
    #: 下调色 —— 绿。
    color_down: str = "#1A9850"
    #: 不显著色 —— 灰。
    color_ns: str = "#AAAAAA"
    #: 画布底色。
    background_color: str = "white"
    #: 网格线透明度（0~1）。
    grid_alpha: float = 0.3

    # ----------------------------- 扩展字段 ----------------------------- #
    #: 坐标轴标签字号（pt）。
    label_font_size: int = 12
    #: 刻度字号（pt）。
    tick_font_size: int = 10
    #: 图例字号（pt）。
    legend_font_size: int = 10
    #: 网格线颜色。
    grid_color: str = "#D0D0D0"
    #: 网格线线型。
    grid_linestyle: str = "--"
    #: 主线条宽度。
    line_width: float = 1.2
    #: 散点默认尺寸（points²）。
    marker_size: float = 28.0
    #: 散点默认透明度。
    scatter_alpha: float = 0.85
    #: 阈值辅助线颜色。
    threshold_color: str = "#666666"
    #: 文字颜色。
    text_color: str = "black"
    #: 分类色板（多组对比/降维分组）。
    palette: tuple[str, ...] = DEFAULT_PALETTE
    #: 字体回退链（``font_family`` 缺失时依次尝试）。
    font_fallbacks: tuple[str, ...] = DEFAULT_FONT_FALLBACKS
    #: ``savefig`` 默认格式。
    save_format: str = "png"
    #: ``savefig`` 裁剪方式（``"tight"`` 或 ``None``）。
    bbox_inches: str | None = "tight"
    #: 主题（``"light"`` / ``"dark"``），仅影响**未被环境变量显式指定**的底色系列字段。
    theme: str = "light"

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        """构造即校验：非法配置尽早失败，而不是等到出图时才报错。"""
        width, height = self.figure_size
        if width <= 0 or height <= 0:
            raise ValueError(f"figure_size 必须为两个正数，当前为 {self.figure_size!r}")
        if self.dpi <= 0:
            raise ValueError(f"dpi 必须为正整数，当前为 {self.dpi!r}")
        if not 0.0 <= self.grid_alpha <= 1.0:
            raise ValueError(f"grid_alpha 应落在 [0, 1]，当前为 {self.grid_alpha!r}")
        if self.theme not in ("light", "dark"):
            raise ValueError(f"theme 只能是 'light' 或 'dark'，当前为 {self.theme!r}")
        if not self.palette:
            raise ValueError("palette 不能为空")

        for name in (
            "color_up", "color_down", "color_ns",
            "background_color", "grid_color", "threshold_color", "text_color",
        ):
            value = getattr(self, name)
            if not _is_color(value):
                raise ValueError(f"{name} 不是合法颜色：{value!r}")

        for name in ("font_size", "title_font_size", "label_font_size",
                     "tick_font_size", "legend_font_size"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须为正数，当前为 {getattr(self, name)!r}")

    # ----------------------------- 便利方法 ----------------------------- #
    def with_overrides(self, **changes: Any) -> VizConfig:
        """返回一份带覆盖项的**新**配置（原对象不变，仍会走校验）。"""
        return _dc_replace(self, **changes)

    def color_for_category(self, category: str) -> str:
        """把分类标签映射到语义色。

        ``"up"`` / ``"上调"`` → :attr:`color_up`；``"down"`` / ``"下调"`` →
        :attr:`color_down`；其余（含 ``"ns"``）→ :attr:`color_ns`。
        """
        key = str(category).strip().lower()
        if key in _UP_ALIASES:
            return self.color_up
        if key in _DOWN_ALIASES:
            return self.color_down
        return self.color_ns

    def category_colors(self) -> dict[str, str]:
        """返回 ``{"up": ..., "down": ..., "ns": ...}`` 三色映射。"""
        return {"up": self.color_up, "down": self.color_down, "ns": self.color_ns}

    def palette_colors(self, n: int) -> list[str]:
        """取前 ``n`` 个分类色（不足则循环取用）。"""
        if n <= 0:
            return []
        base = list(self.palette)
        return [base[i % len(base)] for i in range(n)]

    def as_dict(self) -> dict[str, Any]:
        """转成普通 dict（日志/接口回显/测试断言用）。"""
        return asdict(self)

    def describe(self) -> str:
        """一行摘要，便于启动日志打印。"""
        return (
            f"VizConfig(theme={self.theme}, size={self.figure_size[0]:g}x{self.figure_size[1]:g}in, "
            f"dpi={self.dpi}, font={self.font_family}/{self.font_size}pt, "
            f"up={self.color_up}, down={self.color_down}, ns={self.color_ns}, "
            f"grid_alpha={self.grid_alpha})"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  环境变量解析（非法值 -> 告警 + 回退默认，绝不抛异常）
# ═══════════════════════════════════════════════════════════════════════════
def _env_raw(field: str) -> str | None:
    """读取 ``VIZ_<FIELD>``；空串视作未设置。"""
    raw = os.environ.get(f"{ENV_PREFIX}{field.upper()}")
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _warn(field: str, raw: str, default: object) -> None:
    warnings.warn(
        f"{ENV_PREFIX}{field.upper()}={raw!r} 无法解析，回退默认值 {default!r}",
        RuntimeWarning,
        stacklevel=4,
    )


def _env_str(field: str, default: str) -> str:
    raw = _env_raw(field)
    return raw if raw is not None else default


def _env_opt_str(field: str, default: str | None) -> str | None:
    raw = _env_raw(field)
    if raw is None:
        return default
    # 允许用 "none" / "off" 显式关闭（如 bbox_inches）
    return None if raw.lower() in ("none", "off", "null") else raw


def _env_int(field: str, default: int) -> int:
    raw = _env_raw(field)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _warn(field, raw, default)
        return default


def _env_float(field: str, default: float) -> float:
    raw = _env_raw(field)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        _warn(field, raw, default)
        return default


def _env_size(field: str, default: tuple[float, float]) -> tuple[float, float]:
    """解析 ``"12,9"`` / ``"12x9"`` / ``"12 9"`` 为 ``(12.0, 9.0)``。"""
    raw = _env_raw(field)
    if raw is None:
        return default
    parts = [p for p in re.split(r"[,xX\s]+", raw) if p]
    if len(parts) == 2:
        try:
            values = (float(parts[0]), float(parts[1]))
        except ValueError:
            values = None  # type: ignore[assignment]
        if values is not None and values[0] > 0 and values[1] > 0:
            return values
    _warn(field, raw, default)
    return default


def _from_env() -> VizConfig:
    """由默认值 + 环境变量构造一份配置。"""
    theme = _env_str("theme", "light").lower()
    if theme not in ("light", "dark"):
        _warn("theme", theme, "light")
        theme = "light"

    # 主题只决定"底色系列"的默认值；显式环境变量仍可覆盖它。
    dark = theme == "dark"
    base = VizConfig(
        theme=theme,
        background_color=_DARK_BACKGROUND if dark else "white",
        text_color=_DARK_TEXT if dark else "black",
        grid_color=_DARK_GRID if dark else "#D0D0D0",
    )

    return _dc_replace(
        base,
        figure_size=_env_size("figure_size", base.figure_size),
        dpi=_env_int("dpi", base.dpi),
        font_family=_env_str("font_family", base.font_family),
        font_size=_env_int("font_size", base.font_size),
        title_font_size=_env_int("title_font_size", base.title_font_size),
        label_font_size=_env_int("label_font_size", base.label_font_size),
        tick_font_size=_env_int("tick_font_size", base.tick_font_size),
        legend_font_size=_env_int("legend_font_size", base.legend_font_size),
        color_up=_env_str("color_up", base.color_up),
        color_down=_env_str("color_down", base.color_down),
        color_ns=_env_str("color_ns", base.color_ns),
        background_color=_env_str("background_color", base.background_color),
        grid_alpha=_env_float("grid_alpha", base.grid_alpha),
        grid_color=_env_str("grid_color", base.grid_color),
        grid_linestyle=_env_str("grid_linestyle", base.grid_linestyle),
        line_width=_env_float("line_width", base.line_width),
        marker_size=_env_float("marker_size", base.marker_size),
        scatter_alpha=_env_float("scatter_alpha", base.scatter_alpha),
        threshold_color=_env_str("threshold_color", base.threshold_color),
        text_color=_env_str("text_color", base.text_color),
        save_format=_env_str("save_format", base.save_format),
        bbox_inches=_env_opt_str("bbox_inches", base.bbox_inches),
    )


# ═══════════════════════════════════════════════════════════════════════════
#  单例
# ═══════════════════════════════════════════════════════════════════════════
_CONFIG: VizConfig | None = None
_LOCK = threading.RLock()


def get_config() -> VizConfig:
    """返回全局配置单例（首次调用时解析环境变量，之后直接复用）。

    双重检查锁定：读路径无锁（快），仅在未初始化时进锁，避免多线程下
    重复解析环境变量 / 构造多份配置。
    """
    global _CONFIG
    cfg = _CONFIG
    if cfg is None:
        with _LOCK:
            cfg = _CONFIG
            if cfg is None:
                cfg = _from_env()
                _CONFIG = cfg
    return cfg


def reset_config() -> None:
    """清空单例，使下次 :func:`get_config` 重新读取环境变量（测试专用）。"""
    global _CONFIG
    with _LOCK:
        _CONFIG = None


@contextmanager
def config_override(cfg: VizConfig) -> Iterator[VizConfig]:
    """临时替换全局配置，退出时恢复原值。

    ⚠️ 它改的是**进程级**单例，不是线程局部 —— 因此只适合串行场景
    （脚本、测试、单请求内的临时换肤）。并发场景请改为把 ``cfg`` 显式传给
    各绘图函数（所有绘图函数都支持 ``config=`` 入参）。
    """
    global _CONFIG
    with _LOCK:
        previous = _CONFIG
        _CONFIG = cfg
    try:
        yield cfg
    finally:
        with _LOCK:
            _CONFIG = previous


# ═══════════════════════════════════════════════════════════════════════════
#  matplotlib 桥接
# ═══════════════════════════════════════════════════════════════════════════
def apply_matplotlib_rcparams(cfg: VizConfig | None = None) -> VizConfig:
    """把配置写入 matplotlib 全局 ``rcParams``，返回所用配置。

    这样即便某个绘图函数漏读了配置，其底层默认值也已是统一的 ——
    是"配置中心"的第二道保险。
    """
    import matplotlib

    cfg = cfg or get_config()
    rc = matplotlib.rcParams

    rc["figure.figsize"] = [float(cfg.figure_size[0]), float(cfg.figure_size[1])]
    rc["figure.dpi"] = cfg.dpi
    rc["savefig.dpi"] = cfg.dpi
    rc["figure.facecolor"] = cfg.background_color
    rc["axes.facecolor"] = cfg.background_color
    rc["savefig.facecolor"] = cfg.background_color
    # ⚠️ 必须**双向**写：rcParams 是全局且粘性的。若只写 "tight" 这一支，
    #    一旦某次调用把 savefig.bbox 设成 "tight"，后续拿到 bbox_inches=None
    #    （语义是"不要裁剪"）的配置就再也没法把它还原 —— 出图会被静默裁掉
    #    一圈边框（实测 1000x800 变成 984x784），而 ``save_figure`` 因为只在
    #    非 None 时才传 kwarg，会一路落到这个被污染的 rcParams 上。
    #    matplotlib 的对应取值是 "standard"（即不裁剪）。
    rc["savefig.bbox"] = cfg.bbox_inches if cfg.bbox_inches is not None else "standard"

    # 题目字段直接生效；同时给出缺字回退链（含 CJK 兜底）。
    rc["font.family"] = cfg.font_family
    rc["font.sans-serif"] = [cfg.font_family, *cfg.font_fallbacks]
    rc["font.size"] = cfg.font_size

    rc["axes.titlesize"] = cfg.title_font_size
    rc["axes.labelsize"] = cfg.label_font_size
    rc["xtick.labelsize"] = cfg.tick_font_size
    rc["ytick.labelsize"] = cfg.tick_font_size
    rc["legend.fontsize"] = cfg.legend_font_size

    rc["text.color"] = cfg.text_color
    rc["axes.labelcolor"] = cfg.text_color
    rc["axes.titlecolor"] = cfg.text_color
    rc["xtick.color"] = cfg.text_color
    rc["ytick.color"] = cfg.text_color
    rc["axes.edgecolor"] = cfg.grid_color

    rc["axes.grid"] = True
    rc["axes.axisbelow"] = True
    rc["grid.color"] = cfg.grid_color
    rc["grid.alpha"] = cfg.grid_alpha
    rc["grid.linestyle"] = cfg.grid_linestyle
    rc["grid.linewidth"] = max(cfg.line_width * 0.6, 0.4)

    rc["lines.linewidth"] = cfg.line_width
    rc["legend.frameon"] = False

    return cfg


def new_axes(
    cfg: VizConfig | None = None,
    **subplot_kwargs: Any,
) -> tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
    """按配置创建 ``(fig, ax)``，套用 ``figure_size`` / ``dpi`` / 底色。

    8 个绘图模块的单轴图统一走这里，保证"换个 DPI 全局生效"。
    """
    import matplotlib.pyplot as plt

    cfg = cfg or get_config()
    fig, ax = plt.subplots(
        figsize=tuple(cfg.figure_size),
        dpi=cfg.dpi,
        facecolor=cfg.background_color,
        **subplot_kwargs,
    )
    ax.set_facecolor(cfg.background_color)
    return fig, ax


def save_figure(fig: matplotlib.figure.Figure, path: str | os.PathLike[str],
                *, cfg: VizConfig | None = None, **kwargs: Any) -> str:
    """按配置保存图片（DPI / 底色 / 格式 / 裁剪），返回实际写入路径。"""
    cfg = cfg or get_config()
    target = os.fspath(path)
    kwargs.setdefault("dpi", cfg.dpi)
    kwargs.setdefault("facecolor", cfg.background_color)
    if cfg.bbox_inches is not None:
        kwargs.setdefault("bbox_inches", cfg.bbox_inches)
    fig.savefig(target, **kwargs)
    return target


def sequence_of_str(value: Sequence[str]) -> tuple[str, ...]:
    """把任意字符串序列规整为 tuple（保留给需要自定义调色板的调用方）。"""
    return tuple(str(item) for item in value)

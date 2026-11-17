"""PPT 版式设计系统。

每一页先被描述成与渲染无关的「场景指令」（scene ops），
预览图（PIL）、PDF / PNG 长图与导出的 .pptx 都消费同一份指令，
因此编辑器预览、模板缩略图与实际导出在栅格、字号、留白上保持一致。

坐标单位：英寸；字号单位：pt；颜色：``#RRGGBB``。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REF_DPI = 120  # 版式测量基准：每英寸 120 像素（16:9 → 1600×900）

CN_SANS = "微软雅黑"
CN_SERIF = "宋体"

_FONT_CANDIDATES = {
    ("sans", False): (
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf", 0),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ),
    ("sans", True): (
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf", 0),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ),
    ("serif", False): (
        ("/System/Library/Fonts/Supplemental/Songti.ttc", 6),
        ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 2),
        ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSerifCJKsc-Regular.otf", 0),
    ),
    ("serif", True): (
        ("/System/Library/Fonts/Supplemental/Songti.ttc", 1),
        ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 2),
        ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSerifCJKsc-Bold.otf", 0),
    ),
}
_FALLBACK_FONTS = (
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
)

PAGE_TYPE_LABELS = {
    "cover": "封面",
    "agenda": "目录 / 议程",
    "section": "章节过渡",
    "bullets": "要点列表",
    "metrics": "数据 / 指标",
    "chart": "图表",
    "quote": "金句 / 引用",
    "compare": "对比",
    "end": "结尾",
}
PREVIEW_ORDER = ["cover", "agenda", "section", "bullets", "metrics", "chart", "quote", "compare", "end"]

SCENE_VERSION = 5


# ------------------------------------------------------------------ 颜色
def _clean(hex_color: str) -> str:
    value = (hex_color or "#1A5CC8").lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return value if len(value) == 6 else "1A5CC8"


def rgb(hex_color: str) -> tuple[int, int, int]:
    v = _clean(hex_color)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def hexc(color) -> str:
    if isinstance(color, str):
        return "#" + _clean(color).upper()
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(c))) for c in color)


def mix(a: str, b: str, t: float) -> str:
    ra, ga, ba = rgb(a)
    rb, gb, bb = rgb(b)
    t = max(0.0, min(1.0, t))
    return hexc((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def tint(color: str, t: float) -> str:
    return mix(color, "#FFFFFF", t)


def shade(color: str, t: float) -> str:
    return mix(color, "#000000", t)


# ------------------------------------------------------------------ 字体与排版
@lru_cache(maxsize=512)
def pil_font(role: str, bold: bool, px: int):
    from PIL import ImageFont
    candidates = _FONT_CANDIDATES.get((role, bool(bold)), _FONT_CANDIDATES[("sans", False)])
    for path, index in (*candidates, *_FALLBACK_FONTS):
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, max(6, int(px)), index=index)
        except OSError:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=16384)
def measure(text: str, size: int, bold: bool = False, role: str = "sans") -> float:
    """返回文本宽度（英寸）。"""
    px = max(6, int(round(size * REF_DPI / 72)))
    font = pil_font(role, bold, px)
    try:
        return float(font.getlength(text)) / REF_DPI
    except Exception:  # noqa: BLE001
        return len(text) * size * 0.9 / 72


_NO_START = set("，。、；：？！）】》」』’”%,.;:?!)]}")


def wrap_text(text: str, width: float, size: int, bold: bool = False, role: str = "sans") -> list[str]:
    text = str(text or "").replace("\r", "")
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            if not cur or measure(trial, size, bold, role) <= width:
                cur = trial
            elif ch in _NO_START and cur:
                lines.append(cur + ch)
                cur = ""
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines or [""]


# ------------------------------------------------------------------ 场景
class Scene:
    def __init__(self, w: float, h: float):
        self.w = w
        self.h = h
        self.ops: list[dict] = []

    # -- 基础图元 --------------------------------------------------
    def rect(self, x, y, w, h, fill, radius=0.0, line=None, lw=1.0):
        self.ops.append({"kind": "rect", "x": x, "y": y, "w": w, "h": h,
                         "fill": fill, "radius": max(0.0, radius), "line": line, "lw": lw})
        return self

    def grad(self, x, y, w, h, c1, c2, angle=90):
        self.ops.append({"kind": "grad", "x": x, "y": y, "w": w, "h": h,
                         "c1": c1, "c2": c2, "angle": angle})
        return self

    def ellipse(self, x, y, w, h, fill, line=None, lw=1.0):
        self.ops.append({"kind": "ellipse", "x": x, "y": y, "w": w, "h": h,
                         "fill": fill, "line": line, "lw": lw})
        return self

    def poly(self, points, fill, line=None, lw=1.0):
        self.ops.append({"kind": "poly", "points": [(float(a), float(b)) for a, b in points],
                         "fill": fill, "line": line, "lw": lw})
        return self

    def line(self, x1, y1, x2, y2, color, lw=1.0):
        self.ops.append({"kind": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                         "color": color, "lw": lw})
        return self

    def image(self, x, y, w, h, src):
        self.ops.append({"kind": "image", "x": x, "y": y, "w": w, "h": h, "src": src})
        return self

    def text(self, x, y, w, text, size, color, bold=False, align="left", role="sans", h=0.0):
        self.ops.append({"kind": "text", "x": x, "y": y, "w": w, "h": h, "text": str(text),
                         "size": int(size), "color": color, "bold": bool(bold),
                         "align": align, "role": role})
        return self

    def block(self, x, y, w, text, size, color, bold=False, role="sans", align="left",
              line=1.4, max_lines=None, h=None, valign="top"):
        raw = str(text or "")
        lines = wrap_text(raw, w, size, bold, role)
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            while last and measure(last + "…", size, bold, role) > w:
                last = last[:-1]
            lines[-1] = last + "…"
        lh = size * line / 72.0
        total = lh * len(lines)
        if h is not None:
            if valign == "middle":
                y = y + (h - total) / 2
            elif valign == "bottom":
                y = y + h - total
        for i, ln in enumerate(lines):
            self.ops.append({"kind": "text", "x": x, "y": y + i * lh, "w": w, "h": lh,
                             "text": ln, "size": int(size), "color": color, "bold": bool(bold),
                             "align": align, "role": role})
        return total

    def arc(self, cx, cy, radius, start, end, color, lw=8.0):
        steps = max(6, int(abs(end - start) / 4))
        for i in range(steps):
            a1 = math.radians(start + (end - start) * i / steps)
            a2 = math.radians(start + (end - start) * (i + 1) / steps)
            self.line(cx + radius * math.cos(a1), cy - radius * math.sin(a1),
                      cx + radius * math.cos(a2), cy - radius * math.sin(a2), color, lw)
        return self


# ------------------------------------------------------------------ 模板设计
@dataclass(frozen=True)
class Design:
    id: str
    name: str
    category: str
    style: str
    color_a: str
    color_b: str
    ink: str
    sub: str
    bg: str
    soft: str
    line: str
    accent: str
    accent2: str
    ratio: str = "16:9"
    page_count: int = 12
    use_count: int = 0
    dark: bool = False
    serif: bool = False
    cover: str = "band"
    content: str = "rail"
    agenda: str = "numbered"
    metrics: str = "row"
    chart: str = "bars"
    quote: str = "sidebar"
    compare: str = "twocol"
    end: str = "center"
    deco: str = "plain"
    cover_kicker: str = "MAuto 智能助手平台"
    layout_desc: str = ""


DESIGNS: dict[str, Design] = {d.id: d for d in [
    Design(
        id="tpl_biz_blue", name="商务蓝·年度汇报", category="商务汇报", style="简约",
        color_a="#123A7A", color_b="#2E6BD6", ink="#12213F", sub="#5C6C88",
        bg="#FFFFFF", soft="#EEF3FB", line="#D7E2F4", accent="#1A5CC8", accent2="#7EA8F5",
        use_count=18600, page_count=12, cover="band", content="rail", agenda="numbered",
        metrics="row", chart="bars", quote="sidebar", compare="twocol", end="center",
        deco="grid", cover_kicker="ANNUAL REVIEW · 2026",
        layout_desc="左侧色轨 + 编号要点，稳定的商务栅格，适合年报与经营复盘",
    ),
    Design(
        id="tpl_biz_dark", name="深空·战略发布", category="商务汇报", style="科技",
        color_a="#0A1020", color_b="#1E3A8A", ink="#EAF1FF", sub="#9FB0CC",
        bg="#0B1220", soft="#141F33", line="#26344D", accent="#4F8BFF", accent2="#22D3EE",
        use_count=12300, page_count=15, dark=True, cover="full", content="cards",
        agenda="grid", metrics="tiles", chart="bars", quote="bigtype", compare="twocol",
        end="center", deco="glow", cover_kicker="STRATEGY LAUNCH",
        layout_desc="深色全幅 + 发光网格，卡片承载要点，适合战略发布与科技主题",
    ),
    Design(
        id="tpl_biz_orange", name="活力橙·季度总结", category="商务汇报", style="扁平",
        color_a="#F97316", color_b="#FDBA74", ink="#3B2416", sub="#8B6A57",
        bg="#FFF8F1", soft="#FFE9D6", line="#F3D6BE", accent="#F97316", accent2="#FDBA74",
        use_count=9800, page_count=10, cover="split", content="cards", agenda="cards",
        metrics="tiles", chart="donut", quote="card", compare="twocol", end="block",
        deco="blocks", cover_kicker="QUARTERLY REVIEW",
        layout_desc="左右分栏封面 + 色块卡片，扁平几何，节奏明快，适合季度总结",
    ),
    Design(
        id="tpl_edu_green", name="青禾·课程说课", category="教育教学", style="简约",
        color_a="#0F766E", color_b="#5EEAD4", ink="#103D3A", sub="#5C7C78",
        bg="#FFFFFF", soft="#EAF7F3", line="#CFE8E1", accent="#0F766E", accent2="#5EEAD4",
        ratio="4:3", use_count=7600, page_count=14, cover="topband", content="numbered",
        agenda="timeline", metrics="row", chart="line", quote="card", compare="table",
        end="center", deco="edu", cover_kicker="教学设计与说课",
        layout_desc="顶部色带封面 + 圆形编号步骤，4:3 课堂比例，适合说课与培训",
    ),
    Design(
        id="tpl_edu_yellow", name="暖阳·公开课", category="教育教学", style="扁平",
        color_a="#CA8A04", color_b="#FDE68A", ink="#4A3A12", sub="#8A7434",
        bg="#FFFDF4", soft="#FEF3C7", line="#F2E2B0", accent="#CA8A04", accent2="#F59E0B",
        use_count=5400, page_count=11, cover="full", content="stickies", agenda="cards",
        metrics="circles", chart="bars", quote="card", compare="table", end="block",
        deco="sun", cover_kicker="OPEN CLASS · 公开课",
        layout_desc="暖色便签卡片与圆形指标，轻松亲和，适合公开课与教研分享",
    ),
    Design(
        id="tpl_tech_purple", name="极光·产品发布", category="科技互联网", style="科技",
        color_a="#241046", color_b="#7C3AED", ink="#F1EAFE", sub="#B9A7D9",
        bg="#120A24", soft="#1E1238", line="#35245C", accent="#8B5CF6", accent2="#22D3EE",
        use_count=21400, page_count=16, dark=True, cover="full", content="indexrail",
        agenda="timeline", metrics="tiles", chart="line", quote="bigtype", compare="twocol",
        end="center", deco="orb", cover_kicker="PRODUCT LAUNCH",
        layout_desc="极光渐变与超大序号，左侧视觉锚点 + 右侧内容，适合产品发布",
    ),
    Design(
        id="tpl_tech_cyan", name="矩阵·技术架构", category="科技互联网", style="科技",
        color_a="#0891B2", color_b="#67E8F9", ink="#0B2B33", sub="#4F727B",
        bg="#F4FAFC", soft="#E0F4F8", line="#C7E7EE", accent="#0891B2", accent2="#0E7490",
        use_count=15300, page_count=18, cover="split", content="techgrid", agenda="grid",
        metrics="tiles", chart="bars", quote="sidebar", compare="table", end="center",
        deco="matrix", cover_kicker="SYSTEM ARCHITECTURE",
        layout_desc="网格底纹 + 等距模块，工程感强，适合架构说明与技术方案",
    ),
    Design(
        id="tpl_med_teal", name="康宁·医学学术", category="医疗健康", style="简约",
        color_a="#0E7490", color_b="#A5F3FC", ink="#0F3440", sub="#5D7C85",
        bg="#FFFFFF", soft="#EDF7F9", line="#D3E7EB", accent="#0E7490", accent2="#67E8F9",
        use_count=4800, page_count=13, cover="topband", content="airy", agenda="columns",
        metrics="row", chart="donut", quote="sidebar", compare="table", end="center",
        deco="clinical", cover_kicker="MEDICAL ACADEMIC",
        layout_desc="大面积留白与细线分区，克制专业，适合医学汇报与学术交流",
    ),
    Design(
        id="tpl_gov_red", name="庄重·政务汇报", category="政府政务", style="国潮",
        color_a="#7F1230", color_b="#B91C46", ink="#3A0D12", sub="#7C4A4F",
        bg="#FFFDF9", soft="#FBEAEA", line="#EBC9C9", accent="#9F1239", accent2="#C9A227",
        use_count=6900, page_count=12, serif=True, cover="center", content="centered",
        agenda="numbered", metrics="table", chart="bars", quote="gov", compare="table",
        end="gov", deco="gov", cover_kicker="政 务 工 作 汇 报",
        layout_desc="居中对称 + 红金双线，宋体标题，庄重规范，适合政务汇报",
    ),
    Design(
        id="tpl_gov_gold", name="山河·工作汇报", category="政府政务", style="国潮",
        color_a="#8A5A16", color_b="#C9A227", ink="#3C2A12", sub="#8A6B3E",
        bg="#FBF6EA", soft="#F3E6CA", line="#E0CCA8", accent="#B45309", accent2="#C9A227",
        ratio="4:3", use_count=5200, page_count=14, serif=True, cover="center", content="seal",
        agenda="columns", metrics="scroll", chart="line", quote="gov", compare="twocol",
        end="gov", deco="silk", cover_kicker="年 度 工 作 汇 报",
        layout_desc="绢帛底色 + 山形与印章装饰，宋体竖轴，4:3 版式，适合工作汇报",
    ),
    Design(
        id="tpl_min_gray", name="留白·极简提案", category="简约通用", style="简约",
        color_a="#2B3440", color_b="#94A3B8", ink="#1F2937", sub="#7A8494",
        bg="#FFFFFF", soft="#F4F6F8", line="#E3E7EC", accent="#334155", accent2="#94A3B8",
        use_count=11200, page_count=9, cover="minimal", content="airy", agenda="minimal",
        metrics="plain", chart="plain", quote="bigtype", compare="lines", end="minimal",
        deco="none", cover_kicker="MINIMAL PROPOSAL",
        layout_desc="超大标题 + 极细分割线，几乎无装饰，靠字体层级与留白说话",
    ),
    Design(
        id="tpl_min_ink", name="墨韵·品牌提案", category="简约通用", style="国潮",
        color_a="#2A2824", color_b="#8C8578", ink="#1C1B19", sub="#6B675F",
        bg="#F7F4EE", soft="#EFEAE0", line="#DCD5C8", accent="#1F2937", accent2="#B5532F",
        use_count=8300, page_count=12, serif=True, cover="ink", content="minimaltype",
        agenda="minimal", metrics="ink", chart="plain", quote="ink", compare="lines",
        end="ink", deco="ink", cover_kicker="品 牌 提 案",
        layout_desc="宣纸留白 + 水墨晕染与朱红印章，宋体大标题，适合品牌与文化提案",
    ),
]}


def design_for(template_id: str) -> Design:
    return DESIGNS.get(template_id) or DESIGNS["tpl_biz_blue"]


def canvas_size(ratio: str) -> tuple[float, float]:
    return (13.333, 7.5) if ratio != "4:3" else (10.0, 7.5)


# ------------------------------------------------------------------ 页面类型
def slide_type(slide: dict, index: int, total: int) -> str:
    layout = str(slide.get("layout") or "").strip()
    if layout in PAGE_TYPE_LABELS:
        if layout in ("metrics", "chart", "quote", "compare") and not slide.get(layout):
            return "bullets"
        return layout
    if layout == "content":
        for key in ("metrics", "chart", "quote", "compare"):
            if slide.get(key):
                return key
    if index == 0:
        return "cover"
    if index == total - 1 and str(slide.get("title", "")).strip() in ("谢谢", "谢谢聆听", "感谢聆听", "THE END"):
        return "end"
    return "bullets"


# ------------------------------------------------------------------ 通用部件
def _footer(sc: Scene, d: Design, idx: int, total: int, color=None, align="right"):
    color = color or d.sub
    y = sc.h - 0.55
    sc.line(0.85, y - 0.12, sc.w - 0.85, y - 0.12, mix(d.bg, color, 0.22), 0.6)
    sc.text(0.9, y, sc.w * 0.55, d.name, 9, color, align="left")
    sc.text(sc.w - 2.6, y, 1.75, f"{idx + 1:02d} / {total:02d}", 9, color, align="right")


def _eyebrow(sc: Scene, d: Design, x, y, text, color=None, size=10.5):
    sc.text(x, y, sc.w * 0.6, str(text).upper(), size, color or d.sub, bold=True)


def _bullet_mark(sc: Scene, x, y, kind, color, index=0):
    if kind == "square":
        sc.rect(x, y + 0.08, 0.11, 0.11, color)
    elif kind == "line":
        sc.line(x, y + 0.13, x + 0.26, y + 0.13, color, 2.2)
    elif kind == "dash":
        sc.line(x, y + 0.13, x + 0.18, y + 0.13, color, 1.4)
    elif kind == "circle":
        sc.ellipse(x, y + 0.02, 0.13, 0.13, color)
    elif kind == "number":
        sc.ellipse(x, y - 0.02, 0.34, 0.34, mix(color, d_white := "#FFFFFF", 0.82))
        sc.text(x, y + 0.02, 0.34, f"{index + 1:02d}", 10, color, bold=True, align="center")
    elif kind == "ring":
        sc.ellipse(x, y + 0.02, 0.15, 0.15, None, line=color, lw=1.4)


def _bullets(sc: Scene, d: Design, x, y, w, items, ac, size=16.5, color=None,
             mark="square", gap=0.22, line=1.45, max_items=6, limit_y=None):
    color = color or d.ink
    cy = y
    for i, item in enumerate([str(v) for v in (items or [])][:max_items]):
        mark_w = 0.4 if mark else 0.0
        if mark:
            _bullet_mark(sc, x, cy + 0.03, mark, ac, i)
        h = sc.block(x + mark_w, cy, w - mark_w, item, size, color, line=line, max_lines=2)
        cy += max(h, size * line / 72.0) + gap
        if limit_y is not None and cy > limit_y:
            break
    return cy


def _deco(sc: Scene, d: Design, ac: str, typ: str = "content"):
    """模板级装饰元素（绘制在内容之下）。"""
    W, H = sc.w, sc.h
    if d.deco in ("grid",):
        for i in range(1, 12):
            sc.line(W * i / 12, 0, W * i / 12, H, mix(d.bg, d.ink, 0.05), 0.6)
    elif d.deco == "glow":
        sc.ellipse(W * 0.62, -1.7, 5.4, 5.4, mix(d.bg, ac, 0.08))
        sc.ellipse(W * 0.74, H - 2.4, 4.2, 4.2, mix(d.bg, d.accent2, 0.07))
        sc.line(0, H * 0.18, W, H * 0.18, mix(d.bg, d.line, 0.55), 0.7)
    elif d.deco == "matrix":
        for i in range(0, 15):
            sc.line(W * i / 14, 0, W * i / 14, H, mix(d.bg, d.line, 0.55), 0.5)
        for j in range(0, 9):
            sc.line(0, H * j / 8, W, H * j / 8, mix(d.bg, d.line, 0.55), 0.5)
        sc.rect(W - 2.5, 0.55, 1.6, 0.4, None, line=mix(d.accent, d.bg, 0.25), lw=0.8)
    elif d.deco == "blocks":
        sc.rect(W - 2.2, 0, 2.2, 0.55, d.soft)
        sc.rect(W - 0.7, 0, 0.7, 0.55, ac)
        sc.ellipse(W - 2.5, H - 2.5, 2.0, 2.0, d.soft)
    elif d.deco == "orb":
        sc.ellipse(W - 3.4, -1.5, 4.6, 4.6, mix(d.bg, ac, 0.18))
        sc.ellipse(W - 2.4, H - 2.2, 3.0, 3.0, mix(d.bg, d.accent2, 0.12))
        sc.line(0, H - 0.9, W, H - 0.9, mix(d.bg, ac, 0.25), 0.8)
    elif d.deco == "gov":
        sc.rect(0, 0, W, 0.1, d.accent)
        sc.rect(0, 0.1, W, 0.03, d.accent2)
        sc.rect(0, H - 0.1, W, 0.1, shade(d.accent, 0.12))
    elif d.deco == "silk":
        sc.poly([(0, H), (W * 0.3, H - 1.6), (W * 0.6, H), (W, H - 2.0), (W, H)], mix(d.accent2, d.bg, 0.72))
        sc.poly([(0, H), (W * 0.42, H - 0.9), (W * 0.78, H), (W, H - 1.1), (W, H)], mix(d.accent2, d.bg, 0.52))
        sc.rect(W - 1.5, 0.7, 0.62, 0.62, None, line=d.accent2, lw=2.0)
        sc.text(W - 1.5, 0.83, 0.62, "印", 15, d.accent, bold=True, align="center", role="serif")
    elif d.deco == "ink":
        sc.ellipse(W - 3.6, 0.4, 3.4, 3.4, mix(d.bg, d.soft, 0.7))
        sc.ellipse(W - 2.3, 1.2, 2.0, 2.0, mix(d.bg, d.soft, 0.9))
        sc.line(0.86, 1.55, 0.86, H - 1.0, mix(d.sub, d.bg, 0.55), 0.8)
    elif d.deco == "sun":
        sc.ellipse(W - 2.9, -1.0, 2.8, 2.8, mix(d.bg, d.accent2, 0.22))
        sc.ellipse(W - 1.4, H - 1.8, 1.8, 1.8, mix(d.bg, d.accent2, 0.16))
    elif d.deco == "clinical":
        sc.rect(0, 0, W, 0.09, ac)
        sc.rect(W - 2.4, 0.5, 1.4, 0.03, mix(ac, d.bg, 0.4), 1.0)
        sc.line(W - 1.7, 0.75, W - 1.7, 1.55, mix(ac, d.bg, 0.35), 1.0)
        sc.line(W - 2.0, 1.0, W - 1.4, 1.0, mix(ac, d.bg, 0.35), 1.0)
    elif d.deco == "edu":
        sc.ellipse(W - 2.2, -0.9, 2.0, 2.0, mix(d.bg, d.accent2, 0.2))
        sc.line(0.9, H - 0.85, W - 2.6, H - 0.85, mix(d.bg, d.ink, 0.12), 1.2)


# ------------------------------------------------------------------ 封面
def _cover(sc: Scene, d: Design, slide: dict, idx: int, total: int, ac: str):
    W, H = sc.w, sc.h
    title = slide.get("title") or "未命名演示文稿"
    subtitle = slide.get("subtitle") or ""
    kicker = slide.get("kicker") or d.cover_kicker
    ink = "#FFFFFF" if d.dark or d.cover in ("band", "full", "split") else d.ink

    if d.cover in ("band", "full"):
        sc.grad(0, 0, W, H, d.color_a, shade(d.color_a, 0.35), 90)
        _deco(sc, d, ac, "cover")
        if d.cover == "band":
            sc.rect(0.95, 2.05, 0.12, 1.9, "#FFFFFF")
            sc.text(1.35, 1.5, W - 2.7, kicker, 11, tint(d.color_a, 0.6), bold=True)
            sc.block(1.3, 2.0, W - 2.7, title, 42, "#FFFFFF", bold=True, line=1.16, max_lines=3)
            if subtitle:
                sc.block(1.34, 4.35, W - 3.2, subtitle, 17, tint(d.color_a, 0.78), line=1.5, max_lines=2)
        else:
            _eyebrow(sc, d, 1.15, 1.1, kicker, tint(d.color_a, 0.62), 11.5)
            sc.block(1.1, 2.15, W - 3.6, title, 46, "#FFFFFF", bold=True, line=1.12, max_lines=3)
            sc.rect(1.14, 4.75, 1.5, 0.06, d.accent2)
            if subtitle:
                sc.block(1.14, 5.0, W - 3.4, subtitle, 16, tint(d.color_a, 0.75), line=1.5, max_lines=2)
        sc.text(1.34 if d.cover == "band" else 1.15, H - 1.0, 6.0, "MAuto 智能助手平台", 11,
                tint(d.color_a, 0.55))

    elif d.cover == "split":
        sc.rect(0, 0, W, H, d.bg)
        sc.rect(0, 0, W * 0.42, H, d.soft)
        sc.grad(0, 0, W * 0.42, H, ac, shade(ac, 0.25), 90)
        _deco(sc, d, ac, "cover")
        sc.text(0.75, 0.8, W * 0.3, kicker, 11, tint(ac, 0.55), bold=True)
        sc.text(0.65, H - 2.5, W * 0.34, "01", 96, mix(ac, "#FFFFFF", 0.35), bold=True)
        sc.text(0.78, H - 1.15, W * 0.3, "MAuto", 12, tint(ac, 0.5))
        sc.block(W * 0.42 + 0.95, 1.9, W * 0.58 - 1.9, title, 38, d.ink, bold=True,
                 line=1.18, max_lines=3)
        sc.rect(W * 0.42 + 0.98, 4.15, 1.2, 0.06, ac)
        if subtitle:
            sc.block(W * 0.42 + 0.98, 4.4, W * 0.58 - 2.0, subtitle, 16, d.sub, line=1.55, max_lines=2)
        sc.text(W * 0.42 + 0.98, H - 1.0, 5.0, "MAuto 智能助手平台", 10.5, d.sub)

    elif d.cover == "topband":
        sc.rect(0, 0, W, H, d.bg)
        band_h = 2.9
        sc.grad(0, 0, W, band_h, d.color_a, mix(d.color_a, d.color_b, 0.45), 0)
        _deco(sc, d, ac, "cover")
        sc.text(0.9, 0.55, W - 1.8, kicker, 11, tint(d.color_a, 0.62), bold=True)
        sc.block(0.88, 1.05, W - 1.7, title, 36, "#FFFFFF", bold=True, line=1.2, max_lines=2)
        if subtitle:
            sc.block(0.9, band_h + 0.65, W - 3.2, subtitle, 17, d.ink, line=1.6, max_lines=2)
        sc.rect(0.92, band_h + 2.35, 1.0, 0.05, ac)
        sc.text(0.9, band_h + 2.6, W - 2.0, "MAuto 智能助手平台 · 2026", 11, d.sub)
        sc.text(W - 2.2, H - 1.0, 1.2, "01", 22, d.sub, bold=True, align="right")

    elif d.cover == "center":
        sc.rect(0, 0, W, H, d.bg)
        _deco(sc, d, ac, "cover")
        sc.rect(0, 1.15, W, 0.03, d.accent2)
        sc.rect(0, 1.22, W, 0.02, mix(d.accent, d.bg, 0.35))
        # 对称留白
        sc.text(W * 0.2, 1.7, W * 0.6, kicker, 12, d.accent, align="center", role="serif")
        sc.block(W * 0.12, 2.35, W * 0.76, title, 40, d.ink, bold=True, align="center",
                 line=1.24, max_lines=2, role="serif")
        sc.rect(W / 2 - 0.75, 4.35, 1.5, 0.04, d.accent)
        if subtitle:
            sc.block(W * 0.18, 4.65, W * 0.64, subtitle, 16, d.sub, align="center", line=1.6,
                     max_lines=2, role="serif")
        sc.rect(0, H - 1.25, W, 0.03, d.accent2)
        sc.text(0.9, H - 1.0, 5.0, "MAuto 智能助手平台", 10, d.sub)
        sc.text(W - 2.1, H - 1.0, 1.2, f"{idx + 1:02d}", 10, d.sub, align="right")

    elif d.cover == "minimal":
        sc.rect(0, 0, W, H, d.bg)
        sc.text(W - 3.9, 2.2, 3.2, "01", 120, mix(d.bg, d.ink, 0.06), bold=True, align="right")
        sc.text(0.9, 1.15, W - 2.0, kicker, 10.5, d.sub, bold=True)
        sc.rect(0.92, 1.55, W - 1.9, 0.02, d.ink)
        sc.block(0.9, 1.95, W - 3.2, title, 48, d.ink, bold=True, line=1.18, max_lines=3)
        sc.rect(0.93, 4.95, 0.8, 0.05, ac)
        if subtitle:
            sc.block(0.93, 5.2, W - 3.2, subtitle, 16, d.sub, line=1.6, max_lines=2)
        sc.text(0.9, H - 0.95, 5.0, "MAuto 智能助手平台 · 2026", 10, d.sub)
        sc.text(W - 2.2, H - 0.95, 1.2, f"{idx + 1:02d} / {total:02d}", 10, d.sub, align="right")

    elif d.cover == "ink":
        sc.rect(0, 0, W, H, d.bg)
        _deco(sc, d, ac, "cover")
        sc.text(0.9, 1.1, W - 2.0, kicker, 12, d.sub, role="serif")
        sc.rect(0.92, 1.5, W - 1.9, 0.02, mix(d.ink, d.bg, 0.45))
        sc.block(0.9, 2.0, W - 3.6, title, 46, d.ink, bold=True, line=1.2, max_lines=3, role="serif")
        sc.rect(0.92, 4.75, 0.9, 0.04, d.accent2)
        if subtitle:
            sc.block(0.92, 4.98, W - 3.4, subtitle, 16, d.sub, line=1.6, max_lines=2, role="serif")
        sc.rect(W - 1.55, H - 1.7, 0.58, 0.58, d.accent2)
        sc.text(W - 1.55, H - 1.62, 0.58, "韵", 14, "#FFFFFF", bold=True, align="center", role="serif")
        sc.text(0.9, H - 0.85, 5.0, "MAuto 智能助手平台", 10, d.sub, role="serif")

    if slide.get("image"):
        sc.image(W - 3.4, 4.6, 2.6, 1.6, slide["image"])


# ------------------------------------------------------------------ 要点页
def _content_rail(sc, d, slide, idx, total, ac):
    W, H = sc.w, sc.h
    sc.rect(0, 0, 0.2, H, ac)
    _deco(sc, d, ac)
    _eyebrow(sc, d, 0.95, 0.55, f"{idx + 1:02d} · KEY POINTS", ac)
    sc.block(0.95, 0.9, W - 3.4, slide.get("title") or "", 31, d.ink, bold=True, line=1.2, max_lines=2)
    sc.rect(0.97, 1.95, 1.15, 0.06, ac)
    _bullets(sc, d, 0.95, 2.4, W - 2.2, slide.get("bullets") or [], ac, size=17,
             mark="square", gap=0.3, limit_y=H - 0.95)
    sc.text(W - 4.2, 0.6, 3.3, str(idx + 1).zfill(2), 84, mix(d.bg, d.ink, 0.07),
            bold=True, align="right")
    _footer(sc, d, idx, total)


def _content_cards(sc, d, slide, idx, total, ac):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    _eyebrow(sc, d, 0.9, 0.55, "KEY POINTS", ac if not d.dark else d.accent2)
    title = slide.get("title") or ""
    sc.block(0.9, 0.88, W - 3.6, title, 30, d.ink, bold=True, line=1.2, max_lines=2)
    sc.rect(0.92, 1.92, 1.1, 0.05, ac)
    items = [str(v) for v in (slide.get("bullets") or [])][:6]
    if not items:
        return _footer(sc, d, idx, total)
    cols = 2 if len(items) > 2 else 1
    rows = math.ceil(len(items) / cols)
    left, top = 0.9, 2.35
    gap = 0.24
    cw = (W - 1.8 - gap * (cols - 1)) / cols
    avail_h = H - top - 0.95
    ch = min(1.35, (avail_h - gap * (rows - 1)) / max(rows, 1))
    for i, item in enumerate(items):
        r, c = divmod(i, cols)
        x = left + c * (cw + gap)
        y = top + r * (ch + gap)
        sc.rect(x, y, cw, ch, d.soft if not d.dark else mix(d.bg, ac, 0.1), radius=0.1)
        sc.rect(x, y, 0.07, ch, ac)
        sc.text(x + 0.28, y + 0.16, 0.6, f"{i + 1:02d}", 11, ac, bold=True)
        sc.block(x + 0.28, y + 0.46, cw - 0.5, item, 14.5, d.ink, line=1.45, max_lines=3)
    _footer(sc, d, idx, total)


def _content_numbered(sc, d, slide, idx, total, ac):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    _eyebrow(sc, d, 0.9, 0.52, "STEP BY STEP" if not d.serif else "重 点 梳 理", ac)
    sc.block(0.9, 0.85, W - 2.4, slide.get("title") or "", 30, d.ink, bold=True, line=1.2, max_lines=2)
    sc.rect(0.92, 1.92, 1.1, 0.06, ac)
    items = [str(v) for v in (slide.get("bullets") or [])][:5]
    y = 2.4
    avail = H - y - 0.95
    step = min(1.3, avail / max(len(items), 1))
    for i, item in enumerate(items):
        cy = y + i * step
        sc.ellipse(0.92, cy + 0.02, 0.62, 0.62, ac)
        sc.text(0.92, cy + 0.14, 0.62, f"{i + 1:02d}", 15, "#FFFFFF", bold=True, align="center")
        sc.block(1.8, cy + 0.06, W - 2.7, item, 17, d.ink, line=1.45, max_lines=2)
        if i < len(items) - 1:
            sc.line(1.23, cy + 0.66, 1.23, cy + step, mix(d.line, d.ink, 0.2), 1.2)
    _footer(sc, d, idx, total)


def _content_airy(sc, d, slide, idx, total, ac):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    sc.text(0.92, 0.7, W * 0.5, f"{idx + 1:02d} / {total:02d}", 10, d.sub, bold=True)
    sc.block(0.9, 1.0, W * 0.46, slide.get("title") or "", 34, d.ink, bold=True, line=1.22,
             max_lines=3, role="serif" if d.serif else "sans")
    sc.rect(0.92, 3.0, 0.9, 0.05, ac)
    items = [str(v) for v in (slide.get("bullets") or [])][:5]
    x = W * 0.5 + 0.2
    y = 1.15
    for i, item in enumerate(items):
        sc.text(x, y, 0.5, f"{i + 1:02d}", 10, ac, bold=True)
        h = sc.block(x + 0.62, y - 0.03, W - x - 1.15, item, 15.5, d.ink, line=1.55, max_lines=3)
        y += max(h, 0.4) + 0.34
        sc.line(x, y - 0.14, W - 0.9, y - 0.14, mix(d.line, d.bg, 0.1), 0.7)
    _footer(sc, d, idx, total)


def _content_centered(sc, d, slide, idx, total, ac):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    sc.block(W * 0.1, 0.55, W * 0.8, slide.get("title") or "", 29, d.ink, bold=True,
             align="center", line=1.2, max_lines=2, role="serif")
    sc.rect(W * 0.42, 1.62, W * 0.16, 0.035, d.accent)
    sc.rect(W * 0.44, 1.7, W * 0.12, 0.015, d.accent2)
    items = [str(v) for v in (slide.get("bullets") or [])][:6]
    cols = 2 if len(items) > 3 else 1
    rows = math.ceil(len(items) / cols)
    x0 = W * 0.16
    cw = (W * 0.68) / cols
    y0 = 2.3
    for i, item in enumerate(items):
        r, c = divmod(i, cols)
        x = x0 + c * cw
        y = y0 + r * ((H - y0 - 1.0) / max(rows, 1))
        sc.rect(x, y + 0.07, 0.1, 0.1, d.accent2)
        sc.block(x + 0.25, y, cw - 0.55, item, 15, d.ink, line=1.5, max_lines=2, role="serif")
    _footer(sc, d, idx, total)


def _content_techgrid(sc, d, slide, idx, total, ac):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    sc.text(0.9, 0.45, 2.4, f"[ {idx + 1:02d} ] MODULE", 10, ac, bold=True)
    sc.block(0.88, 0.78, W - 3.0, slide.get("title") or "", 28, d.ink, bold=True, line=1.2, max_lines=2)
    sc.text(W - 2.6, 0.55, 1.7, "SYSTEM / 01", 9, d.sub, align="right")
    items = [str(v) for v in (slide.get("bullets") or [])][:4]
    cols = 2
    rows = max(1, math.ceil(len(items) / cols))
    left, top = 0.9, 2.1
    gap = 0.2
    cw = (W - 1.8 - gap) / cols
    ch = min(1.95, (H - top - 0.95 - gap * (rows - 1)) / max(rows, 1))
    for i, item in enumerate(items):
        r, c = divmod(i, cols)
        x = left + c * (cw + gap)
        y = top + r * (ch + gap)
        sc.rect(x, y, cw, ch, mix(d.bg, "#FFFFFF", 0.55), line=ac, lw=1.0)
        sc.rect(x, y, 0.32, 0.05, ac)
        sc.text(x + 0.2, y + 0.16, 0.6, f"0{i + 1}", 11, ac, bold=True)
        sc.block(x + 0.2, y + 0.5, cw - 0.4, item, 14, d.ink, line=1.45, max_lines=4)
    _footer(sc, d, idx, total)


def _content_stickies(sc, d, slide, idx, total, ac):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    sc.block(0.9, 0.6, W - 3.2, slide.get("title") or "", 29, d.ink, bold=True, line=1.2, max_lines=2)
    sc.rect(0.92, 1.7, 1.0, 0.06, ac)
    items = [str(v) for v in (slide.get("bullets") or [])][:4]
    cols = 2
    gap = 0.28
    left, top = 0.9, 2.1
    cw = (W - 1.8 - gap) / 2
    ch = min(1.9, (H - top - 1.0 - gap) / 2)
    tones = [d.soft, mix(d.soft, d.accent2, 0.22), mix(d.soft, "#FFFFFF", 0.35), mix(d.soft, ac, 0.12)]
    for i, item in enumerate(items):
        r, c = divmod(i, cols)
        x = left + c * (cw + gap)
        y = top + r * (ch + gap)
        sc.rect(x, y, cw, ch, tones[i % len(tones)], radius=0.16)
        sc.rect(x + 0.22, y + 0.2, 0.42, 0.06, ac)
        sc.text(x + 0.22, y + 0.32, 0.6, f"{i + 1:02d}", 10, d.sub, bold=True)
        sc.block(x + 0.22, y + 0.68, cw - 0.44, item, 15, d.ink, line=1.5, max_lines=3)
    _footer(sc, d, idx, total)


def _content_indexrail(sc, d, slide, idx, total, ac):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    sc.text(0.65, 0.45, 2.2, f"CHAPTER {idx + 1:02d}", 10, d.accent2, bold=True)
    sc.text(0.55, 0.75, 2.6, str(idx + 1).zfill(2), 110, mix(d.bg, ac, 0.35), bold=True)
    sc.line(2.95, 1.0, 2.95, H - 1.05, mix(d.line, ac, 0.4), 1.2)
    sc.block(3.25, 0.8, W - 4.1, slide.get("title") or "", 30, d.ink, bold=True, line=1.2, max_lines=2)
    sc.rect(3.28, 2.0, 1.0, 0.05, ac)
    _bullets(sc, d, 3.25, 2.45, W - 4.05, slide.get("bullets") or [], ac,
             size=16.5, mark="ring", gap=0.3, limit_y=H - 0.95)
    _footer(sc, d, idx, total)


def _content_seal(sc, d, slide, idx, total, ac):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    sc.rect(0.88, 0.72, 0.72, 0.72, d.accent)
    sc.text(0.88, 0.86, 0.72, "章", 18, "#FFFFFF", bold=True, align="center", role="serif")
    sc.block(1.9, 0.68, W - 3.1, slide.get("title") or "", 30, d.ink, bold=True,
             line=1.2, max_lines=2, role="serif")
    sc.line(1.92, 1.95, W - 1.0, 1.95, mix(d.accent2, d.ink, 0.25), 1.0)
    sc.line(1.92, 2.02, W - 1.0, 2.02, mix(d.accent2, d.ink, 0.45), 0.5)
    _bullets(sc, d, 1.9, 2.35, W - 2.9, slide.get("bullets") or [], ac,
             size=16, mark="number", gap=0.3, limit_y=H - 0.95)
    _footer(sc, d, idx, total)


def _content_minimaltype(sc, d, slide, idx, total, ac):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    sc.text(0.92, 0.6, W - 2.0, f"{idx + 1:02d} — {d.cover_kicker}", 10, d.sub)
    sc.block(0.9, 1.05, W - 3.0, slide.get("title") or "", 40, d.ink, bold=True,
             line=1.2, max_lines=2, role="serif")
    sc.rect(0.92, 3.0, 0.7, 0.03, d.accent2)
    items = [str(v) for v in (slide.get("bullets") or [])][:5]
    y = 3.4
    for i, item in enumerate(items):
        sc.text(0.92, y, 0.35, "—", 13, d.sub)
        h = sc.block(1.32, y - 0.05, W - 2.4, item, 15, d.ink, line=1.5, max_lines=2, role="serif")
        y += max(h, 0.35) + 0.18
    _footer(sc, d, idx, total)


def _content(sc: Scene, d: Design, slide: dict, idx: int, total: int, ac: str):
    variant = d.content
    if variant == "rail":
        _content_rail(sc, d, slide, idx, total, ac)
    elif variant == "cards":
        _content_cards(sc, d, slide, idx, total, ac)
    elif variant == "numbered":
        _content_numbered(sc, d, slide, idx, total, ac)
    elif variant == "airy":
        _content_airy(sc, d, slide, idx, total, ac)
    elif variant == "centered":
        _content_centered(sc, d, slide, idx, total, ac)
    elif variant == "techgrid":
        _content_techgrid(sc, d, slide, idx, total, ac)
    elif variant == "stickies":
        _content_stickies(sc, d, slide, idx, total, ac)
    elif variant == "indexrail":
        _content_indexrail(sc, d, slide, idx, total, ac)
    elif variant == "seal":
        _content_seal(sc, d, slide, idx, total, ac)
    elif variant == "minimaltype":
        _content_minimaltype(sc, d, slide, idx, total, ac)
    else:
        _content_rail(sc, d, slide, idx, total, ac)


# ------------------------------------------------------------------ 目录
_INDEX_PREFIX = re.compile(
    r"^\s*(?:(?:\d{1,2}|[一二三四五六七八九十]{1,2})\s*[.、,，:：)）]\s*|\d{1,2}\s+)")


def _strip_index(text: str) -> str:
    """去掉「01 / 1. / 一、」这类自带序号，避免与版式序号重复。"""
    return _INDEX_PREFIX.sub("", text, count=1).strip() or text


def _agenda(sc: Scene, d: Design, slide: dict, idx: int, total: int, ac: str):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    items = [_strip_index(str(v)) for v in (slide.get("bullets") or [])][:6] or ["背景与目标", "现状与痛点", "方案总览", "实施路径"]
    variant = d.agenda
    title = slide.get("title") or "目录"

    if variant == "numbered":
        sc.text(0.9, 0.85, 3.0, "CONTENTS", 11, ac, bold=True)
        sc.block(0.88, 1.2, 3.0, title, 36, d.ink, bold=True, line=1.15, max_lines=2)
        sc.rect(0.92, 2.4, 0.9, 0.05, ac)
        x, y = W * 0.44, 1.1
        row_h = min(0.95, (H - 2.0) / max(len(items), 1))
        for i, item in enumerate(items):
            cy = y + i * row_h
            sc.text(x, cy, 0.8, f"{i + 1:02d}", 20, ac, bold=True)
            sc.block(x + 0.95, cy + 0.05, W - x - 1.7, item, 16.5, d.ink, line=1.4, max_lines=1)
            sc.line(x, cy + row_h - 0.12, W - 0.9, cy + row_h - 0.12, d.line, 0.8)

    elif variant == "grid":
        sc.block(0.9, 0.7, W - 2.0, title, 30, d.ink, bold=True, line=1.2, max_lines=1)
        sc.rect(0.92, 1.55, 1.0, 0.05, ac)
        cols, rows = 2, math.ceil(len(items) / 2)
        left, top = 0.9, 1.95
        gap = 0.22
        cw = (W - 1.8 - gap) / 2
        ch = min(1.45, (H - top - 0.95 - gap * (rows - 1)) / max(rows, 1))
        for i, item in enumerate(items):
            r, c = divmod(i, 2)
            x = left + c * (cw + gap)
            y = top + r * (ch + gap)
            sc.rect(x, y, cw, ch, mix(d.bg, ac, 0.1), radius=0.08)
            sc.text(x + 0.25, y + 0.15, 0.8, f"{i + 1:02d}", 22, ac, bold=True)
            sc.block(x + 0.95, y + 0.24, cw - 1.2, item, 15.5, d.ink, line=1.4, max_lines=2)

    elif variant == "columns":
        _eyebrow(sc, d, 0.9, 0.7, "AGENDA", ac)
        sc.block(0.88, 1.0, W * 0.34, title, 40, d.ink, bold=True, line=1.15, max_lines=2, role="serif")
        sc.rect(0.92, 2.9, 0.8, 0.05, ac)
        x = W * 0.46
        y = 0.95
        step = (H - y - 1.0) / max(len(items), 1)
        for i, item in enumerate(items):
            cy = y + i * step
            sc.text(x, cy, 0.6, f"{i + 1:02d}", 12, ac, bold=True)
            sc.block(x + 0.75, cy - 0.04, W - x - 1.4, item, 16, d.ink, line=1.42, max_lines=2,
                     role="serif" if d.serif else "sans")
            sc.line(x, cy + step - 0.12, W - 0.9, cy + step - 0.12, d.line, 0.7)

    elif variant == "timeline":
        sc.block(0.9, 0.65, W - 2.0, title, 30, d.ink, bold=True, line=1.2, max_lines=1)
        sc.rect(0.92, 1.55, 1.0, 0.05, ac)
        n = min(len(items), 5)
        x0, x1 = 1.3, W - 1.3
        y = 3.35
        sc.line(x0, y, x1, y, d.line, 2.0)
        step = (x1 - x0) / max(n - 1, 1)
        for i in range(n):
            cx = x0 + i * step
            sc.ellipse(cx - 0.12, y - 0.12, 0.24, 0.24, ac)
            sc.text(cx - 0.9, y - 0.65, 1.8, f"{i + 1:02d}", 12, ac, bold=True, align="center")
            sc.block(cx - 1.05, y + 0.35, min(2.1, step - 0.1), items[i], 14, d.ink,
                     align="center", line=1.4, max_lines=3)

    elif variant == "cards":
        sc.block(0.9, 0.65, W - 2.0, title, 30, d.ink, bold=True, line=1.2, max_lines=1)
        sc.rect(0.92, 1.5, 1.0, 0.05, ac)
        n = min(len(items), 5)
        gap = 0.2
        cw = (W - 1.8 - gap * (n - 1)) / max(n, 1)
        top, ch = 2.0, 3.6
        for i in range(n):
            x = 0.9 + i * (cw + gap)
            sc.rect(x, top, cw, ch, d.soft, radius=0.1)
            sc.text(x + 0.22, top + 0.2, cw - 0.4, f"{i + 1:02d}", 24, ac, bold=True)
            sc.rect(x + 0.22, top + 0.9, 0.5, 0.04, ac)
            sc.block(x + 0.22, top + 1.15, cw - 0.44, items[i], 14.5, d.ink, line=1.45, max_lines=4)

    else:  # minimal
        sc.text(0.9, 0.75, W - 1.8, "CONTENTS", 10, d.sub, bold=True)
        sc.block(0.88, 1.05, W * 0.45, title, 52, d.ink, bold=True, line=1.1, max_lines=1, role="serif")
        sc.rect(0.92, 2.6, W - 1.9, 0.02, d.ink)
        y = 2.9
        step = (H - y - 1.0) / max(len(items), 1)
        for i, item in enumerate(items):
            cy = y + i * step
            sc.text(0.92, cy, 0.7, f"{i + 1:02d}", 11, d.sub)
            sc.block(1.55, cy - 0.06, W - 2.5, item, 18, d.ink, line=1.4, max_lines=1, role="serif")
            sc.line(1.55, cy + step - 0.2, W - 0.9, cy + step - 0.2, d.line, 0.6)
    _footer(sc, d, idx, total)


# ------------------------------------------------------------------ 章节过渡
def _section(sc: Scene, d: Design, slide: dict, idx: int, total: int, ac: str):
    W, H = sc.w, sc.h
    title = slide.get("title") or "章节标题"
    kicker = slide.get("kicker") or f"PART {idx + 1:02d}"
    lines = [str(v) for v in (slide.get("bullets") or [])][:2]
    num = str(idx + 1).zfill(2)
    head = kicker.split()
    if len(head) >= 2 and head[0].upper() == "PART" and head[-1].isdigit():
        num = head[-1].zfill(2)

    if d.dark:
        sc.grad(0, 0, W, H, d.color_a, shade(d.color_a, 0.45), 90)
        _deco(sc, d, ac, "cover")
        sc.text(0.95, 1.35, 4.0, kicker, 12, d.accent2, bold=True)
        sc.block(0.92, 1.85, W - 2.6, title, 52, "#FFFFFF", bold=True, line=1.12, max_lines=2)
        sc.rect(0.96, 4.25, 1.6, 0.06, ac)
        if lines:
            sc.block(0.96, 4.55, W - 3.2, "　".join(lines), 16, tint(d.color_a, 0.72), line=1.6, max_lines=2)
        sc.text(W - 4.0, H - 1.5, 3.4, num, 110, mix(d.color_a, ac, 0.32),
                bold=True, align="right")

    elif d.deco in ("gov", "silk"):
        sc.rect(0, 0, W, H, d.bg)
        _deco(sc, d, ac, "cover")
        band_y = H * 0.32
        sc.rect(0, band_y, W, 1.9, d.accent)
        sc.rect(0, band_y - 0.05, W, 0.05, d.accent2)
        sc.rect(0, band_y + 1.9, W, 0.05, d.accent2)
        sc.text(0.9, band_y + 0.25, W - 1.8, kicker, 12, tint(d.accent, 0.7), bold=True,
                align="center", role="serif")
        sc.block(0.8, band_y + 0.62, W - 1.6, title, 36, "#FFFFFF", bold=True, align="center",
                 line=1.3, max_lines=1, role="serif")
        if lines:
            sc.block(0.9, band_y + 2.35, W - 1.8, "　".join(lines), 15, d.sub, align="center",
                     line=1.6, max_lines=2, role="serif")

    else:
        sc.rect(0, 0, W, H, d.bg)
        _deco(sc, d, ac, "cover")
        sc.rect(0, 0, W * 0.42, H, d.soft)
        sc.text(0.9, 1.4, 3.4, kicker, 12, ac, bold=True)
        sc.text(0.75, 2.0, 3.4, num, 92, mix(d.soft, ac, 0.45), bold=True)
        sc.block(W * 0.48, 2.55, W * 0.46, title, 36, d.ink, bold=True, line=1.2, max_lines=2)
        sc.rect(W * 0.48, 4.35, 1.2, 0.06, ac)
        if lines:
            sc.block(W * 0.48, 4.65, W * 0.44, "　".join(lines), 15, d.sub, line=1.6, max_lines=2)
    _footer(sc, d, idx, total, "#FFFFFF" if d.dark else None)


# ------------------------------------------------------------------ 数据 / 指标
def _metric_rows(slide: dict) -> list[dict]:
    out = []
    for m in (slide.get("metrics") or [])[:4]:
        if isinstance(m, dict):
            out.append({
                "label": str(m.get("label") or ""),
                "value": str(m.get("value") or ""),
                "unit": str(m.get("unit") or ""),
                "note": str(m.get("note") or ""),
            })
        else:
            out.append({"label": str(m), "value": "", "unit": "", "note": ""})
    return out


def _metrics_page(sc: Scene, d: Design, slide: dict, idx: int, total: int, ac: str):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    rows = _metric_rows(slide)
    title = slide.get("title") or "核心数据"
    variant = d.metrics

    if variant in ("row", "plain", "ink", "scroll"):
        serif = variant in ("ink", "scroll") or d.serif
        sc.block(0.9, 0.62, W - 2.0, title, 29, d.ink, bold=True, line=1.2, max_lines=1, role="serif" if serif else "sans")
        if variant == "plain":
            sc.line(0.92, 1.5, W - 0.9, 1.5, d.ink, 0.8)
        else:
            sc.rect(0.92, 1.55, 1.1, 0.05, ac)
        n = max(len(rows), 1)
        top = 2.3
        cw = (W - 1.8) / n
        for i, m in enumerate(rows):
            x = 0.9 + i * cw
            if variant == "ink":
                sc.rect(x, top - 0.05, cw - 0.45, 3.1, mix(d.bg, d.soft, 0.55), radius=0.04)
                sc.text(x + 0.25, top + 0.1, 0.6, f"{i + 1:02d}", 10, d.accent2, bold=True)
                sc.text(x + 0.25, top + 0.55, cw - 0.7, m["value"] + (" " + m["unit"] if m["unit"] else ""),
                        42, d.ink, bold=True, role="serif")
                sc.text(x + 0.25, top + 1.75, cw - 0.7, m["label"], 14, d.ink, role="serif")
                if m["note"]:
                    sc.block(x + 0.25, top + 2.15, cw - 0.7, m["note"], 10.5, d.sub, line=1.4, max_lines=2, role="serif")
            else:
                size = 56 if variant != "plain" else 64
                sc.text(x, top, cw - 0.35, m["value"], size, ac if variant != "plain" else d.ink,
                        bold=variant != "plain", role="serif" if serif else "sans")
                if m["unit"]:
                    sc.text(x + max(0.9, measure(m["value"], size, variant != "plain", "serif" if serif else "sans")),
                            top + 0.55, cw - 0.4, m["unit"], 14, d.sub, role="serif" if serif else "sans")
                sc.text(x, top + 1.62, cw - 0.3, m["label"], 14, d.ink, bold=True)
                if m["note"]:
                    sc.block(x, top + 1.95, cw - 0.35, m["note"], 10.5, d.sub, line=1.4, max_lines=2)
            if i < n - 1:
                sc.line(x + cw - 0.2, top + 0.05, x + cw - 0.2, top + 3.0, d.line, 1.0)

    elif variant == "tiles":
        sc.block(0.9, 0.62, W - 2.0, title, 29, d.ink, bold=True, line=1.2, max_lines=1)
        sc.rect(0.92, 1.55, 1.1, 0.05, ac)
        n = max(len(rows), 1)
        cols = 2 if n > 2 else n
        rws = math.ceil(n / cols)
        gap = 0.24
        cw = (W - 1.8 - gap * (cols - 1)) / cols
        ch = min(1.95, (H - 2.0 - 1.0 - gap * (rws - 1)) / max(rws, 1))
        for i, m in enumerate(rows):
            r, c = divmod(i, cols)
            x = 0.9 + c * (cw + gap)
            y = 2.0 + r * (ch + gap)
            sc.rect(x, y, cw, ch, mix(d.bg, ac, 0.14), radius=0.12)
            sc.rect(x, y, cw, 0.06, ac)
            sc.text(x + 0.3, y + 0.28, cw - 0.6, m["value"] + (" " + m["unit"] if m["unit"] else ""),
                    38, d.ink, bold=True)
            sc.text(x + 0.3, y + 1.12, cw - 0.6, m["label"], 13.5, d.ink, bold=True)
            if m["note"]:
                sc.block(x + 0.3, y + 1.42, cw - 0.6, m["note"], 10, d.sub, line=1.35, max_lines=1)

    elif variant == "circles":
        sc.block(0.9, 0.62, W - 2.0, title, 29, d.ink, bold=True, line=1.2, max_lines=1)
        sc.rect(0.92, 1.55, 1.1, 0.05, ac)
        n = max(len(rows), 1)
        cw = (W - 1.8) / n
        cy = 3.6
        for i, m in enumerate(rows):
            cx = 0.9 + i * cw + cw / 2
            r = min(1.0, cw / 2 - 0.25)
            sc.ellipse(cx - r, cy - r, r * 2, r * 2, d.soft)
            try:
                pct = max(0.0, min(100.0, float(str(m["value"]).replace("%", ""))))
            except ValueError:
                pct = 68
            sc.arc(cx, cy, r - 0.06, 90, 90 - 360 * pct / 100, ac, lw=9)
            sc.text(cx - r, cy - 0.3, r * 2, m["value"] + m["unit"], 26, d.ink, bold=True, align="center")
            sc.text(cx - cw / 2, cy + r + 0.25, cw, m["label"], 13, d.ink, align="center")
            if m["note"]:
                sc.text(cx - cw / 2, cy + r + 0.62, cw, m["note"], 9.5, d.sub, align="center")

    else:  # table
        sc.block(0.9, 0.62, W - 2.0, title, 29, d.ink, bold=True, line=1.2, max_lines=1, role="serif" if d.serif else "sans")
        sc.rect(0.92, 1.55, 1.1, 0.05, ac)
        top = 2.0
        row_h = min(0.78, (H - top - 1.1) / max(len(rows), 1))
        sc.rect(0.9, top, W - 1.8, row_h, mix(d.bg, ac, 0.12))
        sc.text(1.15, top + 0.18, W * 0.4, "指标", 12, d.ink, bold=True, role="serif" if d.serif else "sans")
        sc.text(W - 2.3, top + 0.18, 1.2, "数值", 12, d.ink, bold=True, align="right", role="serif" if d.serif else "sans")
        for i, m in enumerate(rows):
            y = top + row_h * (i + 1)
            sc.line(0.9, y, W - 0.9, y, d.line, 0.8)
            sc.text(1.15, y + 0.18, W * 0.5, m["label"], 14, d.ink, role="serif" if d.serif else "sans")
            sc.text(W - 2.3, y + 0.13, 2.1, m["value"] + (" " + m["unit"] if m["unit"] else ""),
                    18, ac, bold=True, align="right", role="serif" if d.serif else "sans")
    _footer(sc, d, idx, total)


# ------------------------------------------------------------------ 图表
def _chart_data(slide: dict) -> dict:
    ch = slide.get("chart")
    if not isinstance(ch, dict):
        ch = {}
    cats = [str(c) for c in (ch.get("categories") or ["Q1", "Q2", "Q3", "Q4"])]
    series = []
    for s in (ch.get("series") or [{"name": "数值", "values": [38, 52, 67, 84]}]):
        vals = []
        for v in (s.get("values") or []):
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                vals.append(0.0)
        series.append({"name": str(s.get("name") or ""), "values": vals})
    return {"type": ch.get("type") or "", "categories": cats, "series": series,
            "unit": str(ch.get("unit") or ""), "note": str(ch.get("note") or "")}


def _nice_max(v: float) -> float:
    """把坐标轴上限取整到易读刻度（如 34 → 40）。"""
    if v <= 0:
        return 1.0
    base = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= m * base + 1e-9:
            return m * base
    return 10 * base


def _draw_bars(sc, d, ac, cats, vals, unit, x, y, w, h):
    maxv = _nice_max(max([0.0] + vals))
    for g in range(1, 5):
        gy = y + h - h * g / 4
        sc.line(x, gy, x + w, gy, mix(d.line, d.bg, 0.15), 0.6)
        sc.text(x - 0.65, gy - 0.11, 0.55, f"{maxv * g / 4:.0f}", 8.5, d.sub, align="right")
    slot = w / max(len(vals), 1)
    bw = min(0.95, slot * 0.46)
    for i, v in enumerate(vals):
        cx = x + slot * i + slot / 2
        bh = max(0.04, h * v / maxv)
        sc.rect(cx - bw / 2, y + h - bh, bw, bh, ac if i % 2 == 0 else d.accent2, radius=0.03)
        sc.text(cx - 0.6, y + h - bh - 0.34, 1.2, f"{v:g}{unit}", 10.5, d.ink, bold=True, align="center")
        if i < len(cats):
            sc.text(cx - 0.6, y + h + 0.12, 1.2, cats[i], 10, d.sub, align="center")


def _draw_line(sc, d, ac, cats, vals, unit, x, y, w, h):
    maxv = _nice_max(max([0.0] + vals))
    for g in range(1, 5):
        gy = y + h - h * g / 4
        sc.line(x, gy, x + w, gy, mix(d.line, d.bg, 0.15), 0.6)
        sc.text(x - 0.65, gy - 0.11, 0.55, f"{maxv * g / 4:.0f}", 8.5, d.sub, align="right")
    pts = []
    for i, v in enumerate(vals):
        px = x + w * i / max(len(vals) - 1, 1)
        py = y + h - h * v / maxv
        pts.append((px, py))
    for i in range(len(pts) - 1):
        sc.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], ac, 2.6)
    for i, (px, py) in enumerate(pts):
        sc.ellipse(px - 0.08, py - 0.08, 0.16, 0.16, d.bg, line=ac, lw=2.0)
        sc.text(px - 0.6, py - 0.4, 1.2, f"{vals[i]:g}{unit}", 10, d.ink, bold=True, align="center")
        if i < len(cats):
            sc.text(px - 0.6, y + h + 0.12, 1.2, cats[i], 10, d.sub, align="center")


def _draw_donut(sc, d, ac, cats, vals, unit, x, y, w, h):
    cx, cy = x + w * 0.28, y + h / 2
    r = min(h / 2 - 0.15, w * 0.24)
    total = sum(vals) or 1
    starts = 90.0
    tones = [ac, d.accent2, mix(ac, d.ink, 0.25), mix(d.accent2, d.ink, 0.2)]
    for i, v in enumerate(vals):
        sweep = 360 * v / total
        sc.arc(cx, cy, r - 0.1, starts, starts - sweep, tones[i % len(tones)], lw=18)
        starts -= sweep
    sc.ellipse(cx - r + 0.28, cy - r + 0.28, (r - 0.28) * 2, (r - 0.28) * 2, d.bg)
    sc.text(cx - r, cy - 0.42, r * 2, f"{total:g}{unit}", 22, d.ink, bold=True, align="center")
    sc.text(cx - r, cy + 0.02, r * 2, "合计", 10, d.sub, align="center")
    lx = x + w * 0.56
    ly = y + 0.25
    for i, v in enumerate(vals):
        sc.rect(lx, ly + 0.03, 0.16, 0.16, tones[i % len(tones)])
        label = cats[i] if i < len(cats) else f"项 {i + 1}"
        sc.text(lx + 0.3, ly, w * 0.34, label, 12, d.ink)
        sc.text(lx + w * 0.3, ly, 0.9, f"{v:g}{unit}", 12, ac, bold=True, align="right")
        ly += 0.52


def _draw_hbars(sc, d, ac, cats, vals, unit, x, y, w, h):
    maxv = max([1.0] + vals)
    step = h / max(len(vals), 1)
    for i, v in enumerate(vals):
        cy = y + i * step
        sc.text(x, cy, w * 0.3, cats[i] if i < len(cats) else f"项 {i + 1}", 11, d.ink)
        bw = (w * 0.55) * v / maxv
        sc.rect(x + w * 0.32, cy + 0.04, w * 0.55, 0.18, mix(d.bg, d.line, 0.45))
        sc.rect(x + w * 0.32, cy + 0.04, max(0.02, bw), 0.18, ac if i % 2 == 0 else d.accent2)
        sc.text(x + w * 0.9, cy, w * 0.1, f"{v:g}{unit}", 11, d.ink, bold=True, align="right")


def _chart_page(sc: Scene, d: Design, slide: dict, idx: int, total: int, ac: str):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    data = _chart_data(slide)
    cats, series = data["categories"], data["series"]
    vals = series[0]["values"] if series else []
    unit = data["unit"]
    sc.block(0.9, 0.62, W - 2.0, slide.get("title") or "数据趋势", 29, d.ink, bold=True,
             line=1.2, max_lines=1, role="serif" if d.serif else "sans")
    sub = slide.get("subtitle") or data["note"] or (series[0]["name"] if series else "")
    if sub:
        sc.text(0.92, 1.35, W - 2.0, sub, 11.5, d.sub)
    sc.rect(0.92, 1.55, 1.1, 0.05, ac)
    variant = d.chart
    if variant == "plain":
        _draw_hbars(sc, d, ac, cats, vals, unit, 1.0, 2.2, W - 2.0, 3.6)
    elif variant == "donut":
        _draw_donut(sc, d, ac, cats, vals, unit, 0.9, 2.1, W - 1.8, 3.9)
    elif variant == "line":
        _draw_line(sc, d, ac, cats, vals, unit, 1.45, 2.3, W - 2.6, 3.35)
    else:
        _draw_bars(sc, d, ac, cats, vals, unit, 1.45, 2.3, W - 2.6, 3.35)
    _footer(sc, d, idx, total)


# ------------------------------------------------------------------ 金句
def _quote_data(slide: dict) -> tuple[str, str]:
    q = slide.get("quote")
    if isinstance(q, dict):
        return str(q.get("text") or ""), str(q.get("author") or "")
    if isinstance(q, str) and q.strip():
        return q, str(slide.get("author") or "")
    bullets = [str(v) for v in (slide.get("bullets") or [])]
    return ("　".join(bullets)[:90] or "把重复劳动交给系统，把判断留给人。"), str(slide.get("author") or "")


def _quote_page(sc: Scene, d: Design, slide: dict, idx: int, total: int, ac: str):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    text, author = _quote_data(slide)
    variant = d.quote
    if variant == "bigtype":
        sc.text(1.0, 1.25, 2.0, "“", 120, mix(d.bg, ac, 0.35), bold=True)
        sc.block(1.5, 2.0, W - 3.0, text, 34, d.ink, bold=True, line=1.35, max_lines=4,
                 align="left", role="serif" if d.serif else "sans")
        sc.rect(1.55, 5.55, 0.9, 0.05, ac)
        if author:
            sc.text(1.55, 5.8, W - 3.0, "—— " + author, 13, d.sub)
    elif variant == "card":
        sc.rect(1.1, 1.65, W - 2.2, 3.9, d.soft, radius=0.18)
        sc.rect(1.1, 1.65, 0.12, 3.9, ac)
        sc.text(1.55, 1.95, W - 3.2, "QUOTE", 10, ac, bold=True)
        sc.block(1.55, 2.35, W - 3.3, text, 26, d.ink, line=1.5, max_lines=4,
                 role="serif" if d.serif else "sans")
        if author:
            sc.text(1.55, 4.8, W - 3.2, "—— " + author, 12, d.sub)
    elif variant == "gov":
        sc.rect(W * 0.16, 1.2, W * 0.68, 0.02, d.accent)
        sc.text(W * 0.2, 1.5, W * 0.6, "引 言", 11, d.accent, align="center", role="serif")
        sc.block(W * 0.16, 2.0, W * 0.68, text, 28, d.ink, bold=True, line=1.55, max_lines=4,
                 align="center", role="serif")
        if author:
            sc.text(W * 0.2, 5.15, W * 0.6, "—— " + author, 12, d.sub, align="center", role="serif")
        sc.rect(W * 0.16, 5.65, W * 0.68, 0.02, d.accent)
    elif variant == "ink":
        sc.ellipse(W * 0.58, 1.0, 3.0, 3.0, mix(d.bg, d.soft, 0.8))
        sc.text(0.95, 1.35, 0.9, "“", 76, d.accent2, bold=True, role="serif")
        sc.block(1.5, 1.85, W * 0.6, text, 30, d.ink, bold=True, line=1.5, max_lines=4, role="serif")
        sc.rect(1.55, 5.15, 0.8, 0.03, d.accent2)
        if author:
            sc.text(1.55, 5.4, W * 0.6, "—— " + author, 12, d.sub, role="serif")
    else:  # sidebar
        sc.rect(0.95, 1.4, 0.1, 3.6, ac)
        sc.text(1.25, 1.2, 2.0, "“", 72, mix(d.bg, ac, 0.4), bold=True)
        sc.block(1.3, 1.95, W - 3.0, text, 30, d.ink, bold=True, line=1.45, max_lines=4,
                 role="serif" if d.serif else "sans")
        if author:
            sc.text(1.3, 5.55, W - 3.0, "—— " + author, 13, d.sub)
    _footer(sc, d, idx, total)


# ------------------------------------------------------------------ 对比
def _compare_data(slide: dict) -> dict:
    c = slide.get("compare")
    bullets = [str(v) for v in (slide.get("bullets") or [])]
    if isinstance(c, dict):
        return {
            "left_title": str(c.get("left_title") or "传统方式"),
            "right_title": str(c.get("right_title") or "新方案"),
            "left": [str(v) for v in (c.get("left") or bullets[::2] or ["流程割裂", "重复劳动", "难以复用"])][:4],
            "right": [str(v) for v in (c.get("right") or bullets[1::2] or ["统一入口", "自动执行", "持续沉淀"])][:4],
        }
    return {"left_title": "传统方式", "right_title": "新方案",
            "left": bullets[::2] or ["流程割裂", "重复劳动", "难以复用"],
            "right": bullets[1::2] or ["统一入口", "自动执行", "持续沉淀"]}


def _compare_page(sc: Scene, d: Design, slide: dict, idx: int, total: int, ac: str):
    W, H = sc.w, sc.h
    _deco(sc, d, ac)
    data = _compare_data(slide)
    sc.block(0.9, 0.62, W - 2.0, slide.get("title") or "方案对比", 29, d.ink, bold=True,
             line=1.2, max_lines=1, role="serif" if d.serif else "sans")
    sc.rect(0.92, 1.55, 1.1, 0.05, ac)
    top, bottom = 2.15, H - 1.0
    variant = d.compare
    if variant == "table":
        left, right = 0.9, W / 2 + 0.15
        col_w = W / 2 - 1.15
        headers = [data["left_title"], data["right_title"]]
        for i, (hx, label) in enumerate(((left, headers[0]), (right, headers[1]))):
            sc.rect(hx, top, col_w, 0.55, mix(d.bg, d.accent if i == 0 else ac, 0.14))
            sc.text(hx + 0.2, top + 0.14, col_w - 0.4, label, 13, d.ink, bold=True,
                    align="center", role="serif" if d.serif else "sans")
        rows = max(len(data["left"]), len(data["right"]))
        rh = min(0.85, (bottom - top - 0.7) / max(rows, 1))
        for r in range(rows):
            y = top + 0.55 + r * rh
            sc.line(left, y, W - 0.9, y, d.line, 0.7)
            if r % 2 == 1:
                sc.rect(left, y, col_w, rh, mix(d.bg, d.soft, 0.45))
                sc.rect(right, y, col_w, rh, mix(d.bg, d.soft, 0.45))
            if r < len(data["left"]):
                sc.block(left + 0.25, y + 0.12, col_w - 0.5, data["left"][r], 13.5, d.ink,
                         line=1.4, max_lines=2, role="serif" if d.serif else "sans")
            if r < len(data["right"]):
                sc.block(right + 0.25, y + 0.12, col_w - 0.5, data["right"][r], 13.5, d.ink,
                         line=1.4, max_lines=2, role="serif" if d.serif else "sans")
        sc.line(W / 2, top, W / 2, bottom, mix(d.line, d.ink, 0.12), 1.0)
    elif variant == "lines":
        col_w = (W - 2.0) / 2
        for i, (x, label, items) in enumerate(((0.95, data["left_title"], data["left"]),
                                               (W / 2 + 0.1, data["right_title"], data["right"]))):
            sc.text(x, top, col_w, label, 13, d.sub, bold=True, role="serif")
            sc.line(x, top + 0.42, x + col_w - 0.35, top + 0.42, d.ink, 0.9)
            y = top + 0.75
            for item in items[:4]:
                sc.text(x, y, 0.3, "—", 11, d.sub)
                h = sc.block(x + 0.34, y - 0.06, col_w - 0.6, item, 15, d.ink, line=1.45,
                             max_lines=2, role="serif")
                y += max(h, 0.35) + 0.28
    else:  # twocol
        gap = 0.5
        col_w = (W - 1.8 - gap) / 2
        for i, (label, items, tone) in enumerate(((data["left_title"], data["left"], mix(d.bg, d.line, 0.35)),
                                                  (data["right_title"], data["right"], mix(d.bg, ac, 0.12)))):
            x = 0.9 + i * (col_w + gap)
            sc.rect(x, top, col_w, bottom - top, tone, radius=0.12)
            if i == 1:
                sc.rect(x, top, col_w, 0.07, ac)
            sc.text(x + 0.3, top + 0.25, col_w - 0.6, label, 15, d.ink, bold=True,
                    role="serif" if d.serif else "sans")
            y = top + 0.9
            for item in items[:4]:
                sc.text(x + 0.3, y, 0.3, "·", 16, d.sub, bold=True)
                h = sc.block(x + 0.56, y - 0.02, col_w - 0.9, item, 14, d.ink, line=1.45, max_lines=2)
                y += max(h, 0.32) + 0.24
        sc.ellipse(W / 2 - 0.32, (top + bottom) / 2 - 0.32, 0.64, 0.64, d.bg, line=ac, lw=1.5)
        sc.text(W / 2 - 0.32, (top + bottom) / 2 - 0.12, 0.64, "VS", 11, ac, bold=True, align="center")
    _footer(sc, d, idx, total)


# ------------------------------------------------------------------ 结尾
def _end_page(sc: Scene, d: Design, slide: dict, idx: int, total: int, ac: str):
    W, H = sc.w, sc.h
    title = slide.get("title") or "谢谢聆听"
    subtitle = slide.get("subtitle") or ""
    variant = d.end
    if variant in ("center", "block"):
        sc.grad(0, 0, W, H, d.color_a, shade(d.color_a, 0.32), 90)
        _deco(sc, d, ac, "cover")
        sc.text(W * 0.1, 2.05, W * 0.8, title, 52, "#FFFFFF", bold=True, align="center",
                role="serif" if d.serif else "sans")
        sc.rect(W / 2 - 0.7, 3.55, 1.4, 0.05, d.accent2)
        if subtitle:
            sc.block(W * 0.16, 3.85, W * 0.68, subtitle, 16, tint(d.color_a, 0.75), align="center",
                     line=1.6, max_lines=2)
        sc.text(W * 0.1, H - 1.0, W * 0.8, "MAuto 智能助手平台", 10.5, tint(d.color_a, 0.55), align="center")
    elif variant == "gov":
        sc.rect(0, 0, W, H, d.bg)
        _deco(sc, d, ac, "cover")
        sc.rect(W * 0.14, 1.5, W * 0.72, 0.03, d.accent2)
        sc.text(W * 0.1, 2.15, W * 0.8, title, 46, d.ink, bold=True, align="center", role="serif")
        sc.rect(W * 0.38, 3.55, W * 0.24, 0.04, d.accent)
        if subtitle:
            sc.block(W * 0.2, 3.9, W * 0.6, subtitle, 15, d.sub, align="center", line=1.6,
                     max_lines=2, role="serif")
        sc.rect(W * 0.14, 5.3, W * 0.72, 0.03, d.accent2)
    elif variant == "ink":
        sc.rect(0, 0, W, H, d.bg)
        _deco(sc, d, ac, "cover")
        sc.text(W * 0.1, 2.0, W * 0.8, title, 50, d.ink, bold=True, align="center", role="serif")
        sc.rect(W / 2 - 0.6, 3.55, 1.2, 0.03, d.accent2)
        if subtitle:
            sc.block(W * 0.18, 3.85, W * 0.64, subtitle, 15, d.sub, align="center", line=1.6,
                     max_lines=2, role="serif")
        sc.rect(W - 1.55, H - 1.7, 0.58, 0.58, d.accent2)
        sc.text(W - 1.55, H - 1.62, 0.58, "韵", 14, "#FFFFFF", bold=True, align="center", role="serif")
    else:  # minimal
        sc.rect(0, 0, W, H, d.bg)
        sc.text(0.9, 1.1, W - 2.0, "THE END", 10, d.sub, bold=True)
        sc.block(0.9, 1.9, W - 3.0, title, 58, d.ink, bold=True, line=1.15, max_lines=1, role="serif")
        sc.rect(0.93, 3.7, 1.4, 0.04, ac)
        if subtitle:
            sc.block(0.93, 3.98, W - 3.2, subtitle, 16, d.sub, line=1.6, max_lines=2)
        sc.text(0.9, H - 0.95, W - 2.0, "MAuto 智能助手平台 · 2026", 10, d.sub)
    _footer(sc, d, idx, total, "#FFFFFF" if variant in ("center", "block") else None)


# ------------------------------------------------------------------ 入口
def build_scene(template_id: str, slide: dict, index: int, total: int,
                ratio: str | None = None, accent: str | None = None) -> tuple[Scene, str]:
    d = design_for(template_id)
    ratio = ratio or d.ratio
    w, h = canvas_size(ratio)
    sc = Scene(w, h)
    typ = slide_type(slide or {}, index, total)
    ac = accent or d.accent
    if typ == "cover":
        _cover(sc, d, slide or {}, index, total, ac)
    elif typ == "end":
        _end_page(sc, d, slide or {}, index, total, ac)
    else:
        sc.rect(0, 0, w, h, d.bg)
        if typ == "agenda":
            _agenda(sc, d, slide or {}, index, total, ac)
        elif typ == "section":
            _section(sc, d, slide or {}, index, total, ac)
        elif typ == "metrics":
            _metrics_page(sc, d, slide or {}, index, total, ac)
        elif typ == "chart":
            _chart_page(sc, d, slide or {}, index, total, ac)
        elif typ == "quote":
            _quote_page(sc, d, slide or {}, index, total, ac)
        elif typ == "compare":
            _compare_page(sc, d, slide or {}, index, total, ac)
        else:
            _content(sc, d, slide or {}, index, total, ac)
        if (slide or {}).get("image") and typ in ("bullets",):
            sc.image(w - 4.4, h - 2.8, 3.3, 1.9, slide["image"])
    return sc, typ


# ------------------------------------------------------------------ 示例内容（模板预览）
SAMPLE_SLIDES: list[dict] = [
    {"layout": "cover", "title": "2026 年度经营复盘", "subtitle": "增长策略 · 组织效能 · 下一步规划",
     "kicker": "ANNUAL REVIEW · 2026"},
    {"layout": "agenda", "title": "目录", "bullets": ["年度目标回顾", "关键增长数据", "能力建设进展", "问题与风险", "下一步行动"]},
    {"layout": "section", "title": "增长从哪里来", "kicker": "PART 02",
     "bullets": ["三条增长曲线与投入产出"]},
    {"layout": "bullets", "title": "关键举措与进展",
     "bullets": ["统一客户入口，线索响应从 4 小时缩短到 15 分钟",
                 "自动化周报覆盖 12 个团队，每月节省 260 人时",
                 "项目知识库沉淀 1,800 份资料，复用率提升 2 倍",
                 "重点客户续约率连续三个季度保持在 91% 以上"]},
    {"layout": "metrics", "title": "核心指标",
     "metrics": [{"label": "营收增长", "value": "32", "unit": "%", "note": "同比 · 超额完成 7 个点"},
                 {"label": "客户留存", "value": "91", "unit": "%", "note": "重点客户 · 连续三季度"},
                 {"label": "人均产出", "value": "+18", "unit": "%", "note": "同比 · 组织效能提升"},
                 {"label": "交付周期", "value": "12", "unit": "天", "note": "较年初缩短 40%"}]},
    {"layout": "chart", "title": "季度效率提升趋势", "subtitle": "单位：% · 以年初为基准",
     "chart": {"categories": ["Q1", "Q2", "Q3", "Q4"], "unit": "%",
               "series": [{"name": "效率提升", "values": [18, 27, 39, 52]}]}},
    {"layout": "quote", "quote": {"text": "把重复劳动交给系统，把判断留给人。", "author": "产品负责人"}},
    {"layout": "compare", "title": "传统方式 vs 智能工作台",
     "compare": {"left_title": "传统方式", "right_title": "智能工作台",
                 "left": ["信息分散在多个工具", "重复提问与手工整理", "经验留在个人手里"],
                 "right": ["对话即统一入口", "定时任务自动执行", "知识在项目内沉淀"]}},
    {"layout": "end", "title": "谢谢聆听", "subtitle": "让每一次汇报都有结论"},
]


def sample_slide(page_type: str) -> dict:
    for slide in SAMPLE_SLIDES:
        if slide_type(slide, SAMPLE_SLIDES.index(slide), len(SAMPLE_SLIDES)) == page_type:
            return slide
    return {"layout": "bullets", "title": "示例页面", "bullets": ["要点一", "要点二"]}

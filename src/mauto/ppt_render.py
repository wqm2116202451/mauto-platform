"""PPT 渲染与导出（.pptx / PDF / PNG 长图）。

所有格式都消费 :mod:`mauto.ppt_design` 生成的同一份场景指令：
- 预览图、PDF、PNG 长图由 PIL 执行场景指令；
- .pptx 由 python-pptx 按同一坐标逐条翻译场景指令。
因此模板缩略图、编辑器画布与导出文件使用同一套版式与字号。
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import urllib.request
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

from . import db, ppt_design as pd
from .ppt_design import CN_SANS, CN_SERIF, Scene, build_scene, pil_font, rgb

_FONT_ROLE = {"sans": CN_SANS, "serif": CN_SERIF}
_ALIGN = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}
_PREVIEW_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_IMAGE_CACHE: dict[str, str] = {}


# ------------------------------------------------------------------ 预览缓存
def _cache_put(key: str, data: bytes) -> bytes:
    with _CACHE_LOCK:
        _PREVIEW_CACHE[key] = data
        _PREVIEW_CACHE.move_to_end(key)
        while len(_PREVIEW_CACHE) > 320:
            _PREVIEW_CACHE.popitem(last=False)
    return data


def _cache_key(*parts) -> str:
    raw = "|".join(json.dumps(p, ensure_ascii=False, sort_keys=True) if not isinstance(p, str) else p
                   for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def preview_png(slides: list[dict], template_id: str, ratio: str = "16:9", index: int = 0,
                width: int = 1200, accent: str | None = None) -> bytes:
    width = max(240, min(int(width or 1200), 2000))
    total = len(slides) or 1
    idx = max(0, min(int(index or 0), total - 1))
    key = _cache_key("doc", slides, template_id, ratio, idx, width, accent or "", pd.SCENE_VERSION)
    with _CACHE_LOCK:
        cached = _PREVIEW_CACHE.get(key)
    if cached:
        return cached
    scene, _ = build_scene(template_id, slides[idx], idx, total, ratio, accent)
    return _cache_put(key, render_scene_png(scene, width))


def template_preview_png(template_id: str, page: str = "cover", width: int = 640) -> bytes:
    width = max(240, min(int(width or 640), 2000))
    page = page if page in pd.PAGE_TYPE_LABELS else "cover"
    key = _cache_key("tpl", template_id, page, width, pd.SCENE_VERSION)
    with _CACHE_LOCK:
        cached = _PREVIEW_CACHE.get(key)
    if cached:
        return cached
    d = pd.design_for(template_id)
    slide = pd.sample_slide(page)
    idx = pd.SAMPLE_SLIDES.index(slide) if slide in pd.SAMPLE_SLIDES else 0
    scene, _ = build_scene(template_id, slide, idx, len(pd.SAMPLE_SLIDES), d.ratio)
    return _cache_put(key, render_scene_png(scene, width))


# ------------------------------------------------------------------ PIL 执行器
def render_scene_image(scene: Scene, width: int = 1600) -> Image.Image:
    width = max(120, int(width))
    height = max(1, int(round(width * scene.h / scene.w)))
    scale = width / scene.w
    img = Image.new("RGB", (width, height), _pil("#FFFFFF"))
    draw = ImageDraw.Draw(img)

    for op in scene.ops:
        kind = op["kind"]
        if kind == "rect":
            x, y, w, h = _px_box(op, scale)
            if w <= 0 or h <= 0:
                continue
            radius = int(round(op.get("radius") or 0) * scale)
            line = op.get("line")
            outline = _pil(line) if line else None
            lw = max(1, int(round((op.get("lw") or 1) * scale / 72)))
            if radius > 0:
                draw.rounded_rectangle([x, y, x + w, y + h], radius=min(radius, min(w, h) // 2),
                                       fill=_pil(op["fill"]), outline=outline, width=lw if outline else 1)
            else:
                draw.rectangle([x, y, x + w, y + h], fill=_pil(op["fill"]),
                               outline=outline, width=lw if outline else 1)
        elif kind == "grad":
            _draw_gradient(img, op, scale)
        elif kind == "ellipse":
            x, y, w, h = _px_box(op, scale)
            if w <= 0 or h <= 0:
                continue
            outline = _pil(op["line"]) if op.get("line") else None
            lw = max(1, int(round((op.get("lw") or 1) * scale / 72)))
            draw.ellipse([x, y, x + w, y + h], fill=_pil(op["fill"]), outline=outline,
                         width=lw if outline else 1)
        elif kind == "line":
            lw = max(1, int(round((op.get("lw") or 1) * scale / 72)))
            draw.line([op["x1"] * scale, op["y1"] * scale, op["x2"] * scale, op["y2"] * scale],
                      fill=_pil(op["color"]), width=lw)
        elif kind == "poly":
            pts = [(p[0] * scale, p[1] * scale) for p in op["points"]]
            if len(pts) >= 3:
                draw.polygon(pts, fill=_pil(op["fill"]))
                line = op.get("line")
                if line:
                    lw = max(1, int(round((op.get("lw") or 1) * scale / 72)))
                    draw.line(pts + [pts[0]], fill=_pil(line), width=lw)
        elif kind == "text":
            _draw_text(draw, op, scale)
        elif kind == "image":
            _draw_image(img, op, scale)
    return img


def render_scene_png(scene: Scene, width: int = 1600) -> bytes:
    import io
    img = render_scene_image(scene, width)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _px_box(op, scale):
    return (int(round(op["x"] * scale)), int(round(op["y"] * scale)),
            int(round(op["w"] * scale)), int(round(op["h"] * scale)))


def _pil(color: str):
    if not color:
        return None
    r, g, b = rgb(color)
    return (r, g, b)


def _draw_gradient(img: Image.Image, op: dict, scale: float) -> None:
    x, y, w, h = _px_box(op, scale)
    if w <= 0 or h <= 0:
        return
    c1, c2 = _pil(op["c1"]), _pil(op["c2"])
    horizontal = int(op.get("angle") or 90) % 180 == 0
    band = Image.new("RGB", (1, h) if horizontal else (w, 1))
    draw = ImageDraw.Draw(band)
    span = max(1, h if horizontal else w)
    for i in range(span):
        t = i / max(span - 1, 1)
        color = tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3))
        if horizontal:
            draw.point((0, i), fill=color)
        else:
            draw.point((i, 0), fill=color)
    img.paste(band.resize((w, h)), (x, y))


def _draw_text(draw: ImageDraw.ImageDraw, op: dict, scale: float) -> None:
    text = str(op.get("text") or "")
    if not text:
        return
    px = max(6, int(round(op["size"] * scale / 72)))
    font = pil_font(op.get("role") or "sans", bool(op.get("bold")), px)
    x = op["x"] * scale
    y = op["y"] * scale
    if op.get("align") == "center":
        draw.text((x + op["w"] * scale / 2, y), text, font=font, fill=_pil(op["color"]), anchor="ma")
    elif op.get("align") == "right":
        draw.text((x + op["w"] * scale, y), text, font=font, fill=_pil(op["color"]), anchor="ra")
    else:
        draw.text((x, y), text, font=font, fill=_pil(op["color"]), anchor="la")


def _draw_image(img: Image.Image, op: dict, scale: float) -> None:
    path = _local_image(op.get("src") or "")
    if not path:
        return
    try:
        src = Image.open(path).convert("RGB")
    except Exception:  # noqa: BLE001
        return
    x, y, w, h = _px_box(op, scale)
    if w <= 0 or h <= 0:
        return
    src_ratio = src.width / max(src.height, 1)
    box_ratio = w / max(h, 1)
    if src_ratio > box_ratio:
        new_w = int(src.height * box_ratio)
        left = (src.width - new_w) // 2
        src = src.crop((left, 0, left + new_w, src.height))
    else:
        new_h = int(src.width / box_ratio)
        top = (src.height - new_h) // 2
        src = src.crop((0, top, src.width, top + new_h))
    img.paste(src.resize((w, h)), (x, y))


def _local_image(src: str) -> str | None:
    if not src:
        return None
    if src in _IMAGE_CACHE:
        return _IMAGE_CACHE[src] or None
    local = None
    try:
        if src.startswith(("http://", "https://")):
            digest = hashlib.sha1(src.encode("utf-8")).hexdigest()
            cache_dir = db.DATA_DIR / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            target = cache_dir / f"pptimg_{digest}.img"
            if not target.exists():
                req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6) as resp:  # noqa: S310
                    target.write_bytes(resp.read())
            local = str(target)
        elif Path(src).exists():
            local = src
    except Exception:  # noqa: BLE001
        local = None
    _IMAGE_CACHE[src] = local or ""
    return local


# ------------------------------------------------------------------ PPTX 翻译
def _set_run_font(run, size: int, bold: bool, color: str, role: str = "sans") -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor(*rgb(color))
    face = _FONT_ROLE.get(role, CN_SANS)
    run.font.name = face
    rpr = run._r.get_or_add_rPr()
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    ea = rpr.find(f"{ns}ea")
    if ea is None:
        from lxml import etree
        ea = etree.SubElement(rpr, f"{ns}ea")
    ea.set("typeface", face)


def _apply_shape_style(shape, fill: str | None, line: str | None, lw: float) -> None:
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(*rgb(fill))
    else:
        shape.fill.background()
    if line:
        shape.line.color.rgb = RGBColor(*rgb(line))
        shape.line.width = Pt(max(0.5, lw))
    else:
        shape.line.fill.background()
    try:
        shape.shadow.inherit = False
    except Exception:  # noqa: BLE001
        pass


def _add_text(slide, op: dict) -> None:
    text = str(op.get("text") or "")
    if not text:
        return
    h = max(op["h"], op["size"] * 1.35 / 72)
    box = slide.shapes.add_textbox(Inches(op["x"]), Inches(op["y"]),
                                   Inches(max(op["w"], 0.2)), Inches(h))
    tf = box.text_frame
    tf.word_wrap = False
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.TOP
    p = tf.paragraphs[0]
    p.alignment = _ALIGN.get(op.get("align") or "left", PP_ALIGN.LEFT)
    p.line_spacing = 1.0
    p.space_before = Pt(0)
    p.space_after = Pt(0)
    run = p.add_run()
    run.text = text
    _set_run_font(run, op["size"], bool(op.get("bold")), op["color"], op.get("role") or "sans")


def _add_gradient(slide, op: dict) -> None:
    from pptx.enum.shapes import MSO_SHAPE
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(op["x"]), Inches(op["y"]),
                                   Inches(op["w"]), Inches(op["h"]))
    try:
        shape.fill.gradient()
        shape.fill.gradient_angle = float(op.get("angle") or 90)
        stops = list(shape.fill.gradient_stops)
        if len(stops) >= 2:
            stops[0].color.rgb = RGBColor(*rgb(op["c1"]))
            stops[0].position = 0.0
            stops[-1].color.rgb = RGBColor(*rgb(op["c2"]))
            stops[-1].position = 1.0
    except Exception:  # noqa: BLE001
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(*rgb(op["c1"]))
    shape.line.fill.background()
    try:
        shape.shadow.inherit = False
    except Exception:  # noqa: BLE001
        pass


def _add_rect(slide, op: dict) -> None:
    from pptx.enum.shapes import MSO_SHAPE
    x, y, w, h = op["x"], op["y"], max(op["w"], 0.01), max(op["h"], 0.01)
    radius = float(op.get("radius") or 0)
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius > 0.01 else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            shape.adjustments[0] = min(0.5, radius / min(w, h))
        except Exception:  # noqa: BLE001
            pass
    _apply_shape_style(shape, op.get("fill"), op.get("line"), float(op.get("lw") or 1))


def _add_freeform(slide, op: dict, close: bool = True):
    pts = op.get("points") or []
    if len(pts) < 3:
        return
    emu = [(int(round(p[0] * 914400)), int(round(p[1] * 914400))) for p in pts]
    builder = slide.shapes.build_freeform(emu[0][0], emu[0][1], scale=1.0)
    builder.add_line_segments(emu[1:], close=close)
    shape = builder.convert_to_shape()
    _apply_shape_style(shape, op.get("fill"), op.get("line"), float(op.get("lw") or 1))
    return shape


def _add_line(slide, op: dict) -> None:
    from pptx.enum.shapes import MSO_CONNECTOR
    x1, y1, x2, y2 = (int(round(op[k] * 914400)) for k in ("x1", "y1", "x2", "y2"))
    if abs(x1 - x2) < 100 and abs(y1 - y2) < 100:
        return
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Emu(x1), Emu(y1), Emu(x2), Emu(y2))
    conn.line.color.rgb = RGBColor(*rgb(op["color"]))
    conn.line.width = Pt(max(0.5, float(op.get("lw") or 1)))
    try:
        conn.shadow.inherit = False
    except Exception:  # noqa: BLE001
        pass


def _add_image(slide, op: dict) -> None:
    path = _local_image(op.get("src") or "")
    if not path:
        return
    try:
        slide.shapes.add_picture(path, Inches(op["x"]), Inches(op["y"]),
                                 Inches(op["w"]), Inches(op["h"]))
    except Exception:  # noqa: BLE001
        pass


def _scene_to_pptx(slide, scene: Scene) -> None:
    from pptx.enum.shapes import MSO_SHAPE
    for op in scene.ops:
        kind = op["kind"]
        try:
            if kind == "rect":
                _add_rect(slide, op)
            elif kind == "grad":
                _add_gradient(slide, op)
            elif kind == "ellipse":
                shape = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(op["x"]), Inches(op["y"]),
                                               Inches(max(op["w"], 0.01)), Inches(max(op["h"], 0.01)))
                _apply_shape_style(shape, op.get("fill"), op.get("line"), float(op.get("lw") or 1))
            elif kind == "line":
                _add_line(slide, op)
            elif kind == "poly":
                _add_freeform(slide, op, close=True)
            elif kind == "text":
                _add_text(slide, op)
            elif kind == "image":
                _add_image(slide, op)
        except Exception:  # noqa: BLE001
            # 单个图形失败不应中断整页导出
            continue


# ------------------------------------------------------------------ 文档渲染
def _slides_of(doc: dict) -> list[dict]:
    content = doc.get("content_json")
    if isinstance(content, str):
        return db.jload(content, [])
    return content or []


def render_images(doc: dict) -> list[Image.Image]:
    slides = _slides_of(doc)
    ratio = doc.get("ratio", "16:9")
    width = 1600 if ratio != "4:3" else 1200
    accent = doc.get("cover_color") or None
    images = []
    for idx, slide in enumerate(slides):
        scene, _ = build_scene(doc.get("template_id") or "tpl_biz_blue", slide, idx, len(slides),
                               ratio, accent)
        images.append(render_scene_image(scene, width))
    return images


def build_pptx(doc: dict, path: Path) -> Path:
    slides = _slides_of(doc)
    ratio = doc.get("ratio", "16:9")
    width, height = pd.canvas_size(ratio)
    prs = Presentation()
    prs.slide_width = Emu(int(Inches(width)))
    prs.slide_height = Emu(int(Inches(height)))
    blank = prs.slide_layouts[6]
    accent = doc.get("cover_color") or None
    for idx, slide_data in enumerate(slides):
        slide = prs.slides.add_slide(blank)
        scene, _ = build_scene(doc.get("template_id") or "tpl_biz_blue", slide_data, idx, len(slides),
                               ratio, accent)
        _scene_to_pptx(slide, scene)
        if slide_data.get("notes"):
            slide.notes_slide.notes_text_frame.text = str(slide_data["notes"])
    prs.save(str(path))
    return path


def build_pdf(doc: dict, path: Path) -> Path:
    images = render_images(doc)
    if images:
        images[0].save(str(path), save_all=True, append_images=images[1:], resolution=144.0)
    return path


def build_png(doc: dict, path: Path) -> Path:
    images = render_images(doc)
    if not images:
        return path
    gap = 24
    canvas = Image.new("RGB", (max(i.width for i in images),
                               sum(i.height for i in images) + gap * (len(images) - 1)), "#EDF1F8")
    y = 0
    for img in images:
        canvas.paste(img, (0, y))
        y += img.height + gap
    canvas.save(str(path))
    return path


# 兼容旧接口
FONT_CN = CN_SANS
_ = math

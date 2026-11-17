"""文件解析：按扩展名提取可读文本，解析失败时回退为占位说明。"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

TEXT_EXT = {".txt", ".md", ".markdown", ".json", ".csv", ".log", ".py", ".js", ".html", ".css"}


def _pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    pages = [(p.extract_text() or "") for p in reader.pages[:30]]
    return "\n".join(pages)


def _docx(path: Path) -> str:
    import docx
    doc = docx.Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables[:5]:
        for row in table.rows[:10]:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _xlsx(path: Path) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets[:3]:
        lines.append(f"[工作表] {ws.title}")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i > 80:
                break
            lines.append(" | ".join("" if v is None else str(v) for v in row))
    wb.close()
    return "\n".join(lines)


def _pptx(path: Path) -> str:
    from pptx import Presentation
    prs = Presentation(str(path))
    lines = []
    for idx, slide in enumerate(prs.slides, 1):
        lines.append(f"[第 {idx} 页]")
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                lines.append(shape.text_frame.text.strip())
    return "\n".join(lines)


def extract_text(path: Path, filename: str) -> tuple[str, str]:
    """返回 (解析状态, 文本)。状态：parsed / failed。"""
    ext = Path(filename).suffix.lower()
    try:
        if ext in TEXT_EXT:
            raw = path.read_bytes()[:200_000]
            for enc in ("utf-8", "gb18030", "latin-1"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = raw.decode("utf-8", errors="ignore")
            if ext == ".csv":
                rows = list(csv.reader(io.StringIO(text)))[:60]
                text = "\n".join(" | ".join(r) for r in rows)
            return "parsed", text[:20000]
        if ext == ".pdf":
            return "parsed", _pdf(path)[:20000]
        if ext == ".docx":
            return "parsed", _docx(path)[:20000]
        if ext in {".xlsx", ".xlsm"}:
            return "parsed", _xlsx(path)[:20000]
        if ext == ".pptx":
            return "parsed", _pptx(path)[:20000]
        if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            return "parsed", f"[图片文件] {filename}，已纳入多模态理解范围（演示环境不执行 OCR）。"
        return "parsed", f"[{ext or '未知'} 文件] {filename}"
    except Exception as exc:  # noqa: BLE001 - 解析失败需降级而不是中断
        return "failed", f"解析失败：{type(exc).__name__}"


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit != "GB" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size}B"


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)

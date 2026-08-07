"""
Markdown → PDF, đủ dùng cho báo cáo gửi qua Telegram.

Cố ý tối giản: heading, đoạn văn, gạch đầu dòng, khối code, đường kẻ. Không bảng,
không ảnh nhúng. Ai cần bản đẹp thì render HTML rồi in — thư viện đó nặng gấp mười
và kéo theo cả một runtime trình duyệt.

`reportlab` là dependency tuỳ chọn. Không cài thì hàm ném `PDFUnavailable` với câu
hướng dẫn cụ thể, thay vì `ImportError` trần trụi ở giữa một lượt chat.
"""

from __future__ import annotations

import re
from pathlib import Path


class PDFUnavailable(RuntimeError):
    pass


def _styles():
    try:
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    except ImportError as exc:  # noqa: PERF203
        raise PDFUnavailable(
            "Chưa cài reportlab. Chạy: pip install 'alice-telegram-session[pdf]'"
        ) from exc

    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=18, spaceAfter=12),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=14, spaceAfter=8),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=12, spaceAfter=6),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontSize=10,
                               leading=15, alignment=TA_LEFT, spaceAfter=6),
        "code": ParagraphStyle("code", parent=base["Code"], fontSize=8.5,
                               leading=11, backColor="#f4f4f6",
                               borderPadding=6, spaceAfter=8),
        "li": ParagraphStyle("li", parent=base["BodyText"], fontSize=10,
                             leading=15, leftIndent=14, spaceAfter=3),
    }


def _inline(text: str) -> str:
    """Markdown inline → thẻ reportlab. Escape XML TRƯỚC, nếu không một dấu `<`
    trong nội dung sẽ làm hỏng cả trang."""
    out = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    out = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<link href="\2"><u>\1</u></link>', out)
    return out


def markdown_to_pdf(markdown: str, dest: Path, title: str = "") -> Path:
    """Render `markdown` ra `dest`. Trả về `dest`."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

    styles = _styles()
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(dest), pagesize=A4, title=title or dest.stem,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
    )

    flow = []
    if title:
        flow += [Paragraph(_inline(title), styles["h1"]),
                 HRFlowable(width="100%", thickness=0.5, color="#cccccc"),
                 Spacer(1, 8)]

    in_code, buffer = False, []
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                flow.append(Paragraph("<br/>".join(
                    l.replace("&", "&amp;").replace("<", "&lt;").replace(" ", "&nbsp;")
                    for l in buffer), styles["code"]))
                buffer = []
            in_code = not in_code
            continue
        if in_code:
            buffer.append(line)
            continue

        stripped = line.strip()
        if not stripped:
            flow.append(Spacer(1, 4))
        elif stripped.startswith("### "):
            flow.append(Paragraph(_inline(stripped[4:]), styles["h3"]))
        elif stripped.startswith("## "):
            flow.append(Paragraph(_inline(stripped[3:]), styles["h2"]))
        elif stripped.startswith("# "):
            flow.append(Paragraph(_inline(stripped[2:]), styles["h1"]))
        elif re.match(r"^[-*+]\s+", stripped):
            flow.append(Paragraph("• " + _inline(re.sub(r"^[-*+]\s+", "", stripped)), styles["li"]))
        elif re.match(r"^\d+[.)]\s+", stripped):
            flow.append(Paragraph(_inline(stripped), styles["li"]))
        elif set(stripped) <= {"-", "*", "_"} and len(stripped) >= 3:
            flow.append(HRFlowable(width="100%", thickness=0.5, color="#dddddd"))
        else:
            flow.append(Paragraph(_inline(stripped), styles["body"]))

    if in_code and buffer:  # khối code không đóng — vẫn phải in ra
        flow.append(Paragraph("<br/>".join(buffer), styles["code"]))

    doc.build(flow)
    return dest

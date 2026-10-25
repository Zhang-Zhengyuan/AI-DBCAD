"""Build the research proposal from its Markdown source with ReportLab.

Usage: python docs/research/build_proposal.py [--font /path/to/CJK-font.ttf]
ReportLab is an authoring dependency, separate from the application runtime.
"""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path
import re
from urllib.parse import quote

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Flowable, PageBreak, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/research/RESEARCH_PROPOSAL.md"
OUTPUT = ROOT / "output/pdf/resumacad-research-proposal.pdf"
INK = colors.HexColor("#23313E")
ACCENT = colors.HexColor("#176873")
MUTED = colors.HexColor("#5D6973")
PALE = colors.HexColor("#F0F5F6")
RULE = colors.HexColor("#D6E0E3")
WIDTH = A4[0] - 96


def register_font(path: str | None) -> str:
    candidates = [Path(path)] if path else [Path("/Library/Fonts/Arial Unicode.ttf")]
    for candidate in candidates:
        if candidate.is_file():
            pdfmetrics.registerFont(TTFont("ProposalCJK", str(candidate)))
            font = "ProposalCJK"
            break
    else:
        if path:
            raise FileNotFoundError(path)
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        font = "STSong-Light"
    pdfmetrics.registerFontFamily(font, normal=font, bold=font, italic=font, boldItalic=font)
    return font


def inline(text: str) -> str:
    text = escape(text)

    def link(match):
        label, href = match.groups()
        if not href.startswith(("https://", "http://", "#")):
            resolved = (SOURCE.parent / href.split("#", 1)[0]).resolve()
            relative = resolved.relative_to(ROOT).as_posix()
            href = "https://github.com/Zhang-Zhengyuan/ResumaCAD/blob/main/" + quote(relative)
        return f'<link href="{href}" color="#176873">{label}</link>'

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r'<font color="#344F60">\1</font>', text)
    text = re.sub(r"\[(\d+)\]", r'<link href="#ref-\1" color="#176873">[\1]</link>', text)
    text = text.replace("[L1]", '<link href="#ref-local" color="#176873">[L1]</link>')
    return text


class Figure(Flowable):
    def __init__(self, kind, font):
        super().__init__()
        self.kind, self.font = kind, font
        self.width = WIDTH
        self.height = 154 if kind == "baseline" else 104 if kind == "method" else 124

    def arrow(self, x1, y1, x2, y2):
        c = self.canv
        c.setStrokeColor(ACCENT)
        c.setFillColor(ACCENT)
        c.setLineWidth(1)
        c.line(x1, y1, x2, y2)
        direction = 1 if x2 > x1 else -1
        path = c.beginPath()
        path.moveTo(x2, y2)
        path.lineTo(x2 - direction * 4, y2 + 2.5)
        path.lineTo(x2 - direction * 4, y2 - 2.5)
        path.close()
        c.drawPath(path, fill=1, stroke=0)

    def box(self, x, y, width, lines):
        c = self.canv
        c.setFillColor(PALE)
        c.setStrokeColor(RULE)
        c.roundRect(x, y, width, 44, 3, fill=1, stroke=1)
        c.setFillColor(INK)
        c.setFont(self.font, 9.2)
        for index, line in enumerate(lines):
            c.drawCentredString(x + width / 2, y + 27 - 14 * index, line)

    def draw(self):
        c = self.canv
        c.setFont(self.font, 9)
        if self.kind == "baseline":
            left, plot_width = 156, WIDTH - 214
            c.setFillColor(MUTED)
            c.drawString(0, 139, "既有单轮对照：可用实体率")
            for tick in (0, 25, 50, 75, 100):
                x = left + plot_width * tick / 100
                c.setStrokeColor(RULE)
                c.line(x, 25, x, 123)
                c.setFillColor(MUTED)
                c.drawCentredString(x, 12, str(tick))
            rows = [("AgenticCADedit", 18), ("IterCAD-STEP 适配", 13), ("neuralCAD 脚本", 6)]
            for idx, (label, count) in enumerate(rows):
                y = 103 - idx * 32
                c.setFillColor(INK)
                c.drawString(0, y + 3, label)
                c.setFillColor(ACCENT if idx == 0 else colors.HexColor("#839AA3"))
                c.rect(left, y, plot_width * count / 24, 16, fill=1, stroke=0)
                c.setFillColor(INK)
                c.drawRightString(WIDTH, y + 3, f"{count}/24")
            c.setFillColor(MUTED)
            c.drawCentredString(left + plot_width / 2, 0, "占全部请求的百分比 (%)")
        elif self.kind == "benchmark":
            labels = [["输入模型", "当前有效要求"], ["被测系统", "观测与编辑"], ["独立验收", "目标 / 保护项"], ["下一阶段", "继承实际输出"]]
            gap = 14
            width = (WIDTH - 3 * gap) / 4
            for idx, lines in enumerate(labels):
                x = idx * (width + gap)
                self.box(x, 47, width, lines)
                if idx < 3:
                    self.arrow(x + width + 1, 69, x + width + gap - 1, 69)
            c.setFillColor(MUTED)
            c.drawString(0, 108, "逐阶段开放请求和外部事件；统一观测与操作权限")
            c.setStrokeColor(ACCENT)
            c.line(WIDTH - width / 2, 45, WIDTH - width / 2, 26)
            c.line(WIDTH - width / 2, 26, width / 2, 26)
            c.line(width / 2, 26, width / 2, 45)
            c.setFillColor(MUTED)
            c.drawCentredString(WIDTH / 2, 4, "项目状态持续演化；固定检查点诊断另设独立轨道")
        else:
            labels = [["来源版本", "编辑 / 事件"], ["判断依赖", "检查有效条件"], ["按需调用", "几何内核"], ["当前证据", "重建计划"], ["继续编辑", "校验提交版本"]]
            gap = 12
            width = (WIDTH - 4 * gap) / 5
            for idx, lines in enumerate(labels):
                x = idx * (width + gap)
                self.box(x, 30, width, lines)
                if idx < 4:
                    self.arrow(x + width + 1, 52, x + width + gap - 1, 52)
            c.setFillColor(MUTED)
            c.drawString(0, 91, "统一证据协议：查询、实例、参考系、单位、依赖、覆盖与来源")
            c.drawCentredString(WIDTH / 2, 13, "证据不足或对应不确定时返回未知并重验；历史观测保持可追溯")
            c.drawCentredString(WIDTH / 2, 0, "候选机制，尚未实现或验证")


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, font, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved_states, self.body_font = [], font

    def showPage(self):
        self.saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self.saved_states)
        for state in self.saved_states:
            self.__dict__.update(state)
            self.setStrokeColor(RULE)
            self.setLineWidth(0.6)
            self.line(48, A4[1] - 37, A4[0] - 48, A4[1] - 37)
            self.setFont("Helvetica", 8)
            self.setFillColor(ACCENT)
            self.drawString(48, A4[1] - 26, "RESUMACAD / RESEARCH PROPOSAL")
            self.setFillColor(MUTED)
            self.drawRightString(A4[0] - 48, A4[1] - 26, "2026.10.25 / v1.0")
            self.setFont(self.body_font, 8)
            self.drawString(48, 26, "选题与预实验计划 | 已有事实与待验证假设分列")
            self.drawRightString(A4[0] - 48, 26, f"{self._pageNumber} / {total}")
            super().showPage()
        super().save()


def build(font_path=None):
    font = register_font(font_path)
    body = ParagraphStyle("body", fontName=font, fontSize=10.2, leading=16.1,
                          textColor=INK, wordWrap="CJK", spaceAfter=8, alignment=TA_LEFT)
    styles = {
        "body": body,
        "title": ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=30, leading=38, textColor=ACCENT, spaceAfter=14),
        "h2": ParagraphStyle("h2", parent=body, fontSize=17, leading=25, textColor=ACCENT, spaceAfter=15, keepWithNext=True),
        "h3": ParagraphStyle("h3", parent=body, fontSize=11.5, leading=17, spaceBefore=7, spaceAfter=7, keepWithNext=True),
        "cell": ParagraphStyle("cell", parent=body, fontSize=9.2, leading=14.2, spaceAfter=0),
        "caption": ParagraphStyle("caption", parent=body, fontSize=8.6, leading=12.8, textColor=MUTED, spaceAfter=10),
        "quote": ParagraphStyle("quote", parent=body, fontSize=11, leading=18, textColor=ACCENT, leftIndent=12, rightIndent=10, borderPadding=10, backColor=PALE, spaceAfter=14),
        "bullet": ParagraphStyle("bullet", parent=body, leftIndent=15, firstLineIndent=0, bulletIndent=0, spaceAfter=7),
    }
    story = []
    lines = SOURCE.read_text().splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            idx += 1
            continue
        if line == "<!-- page -->":
            story.append(PageBreak())
            idx += 1
        elif line.startswith("<!-- figure:"):
            kind = re.search(r"figure: (\w+)", line).group(1)
            story.append(Figure(kind, font))
            story.append(Spacer(1, 5))
            idx += 1
        elif line.startswith("|"):
            rows = []
            while idx < len(lines) and lines[idx].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[idx].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append([Paragraph(inline(c), styles["cell"]) for c in cells])
                idx += 1
            count = len(rows[0])
            proportions = {2: [0.26, 0.74], 3: [0.24, 0.40, 0.36]}[count]
            table = Table(rows, colWidths=[WIDTH * p for p in proportions], repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PALE),
                ("LINEBELOW", (0, 0), (-1, 0), 0.9, ACCENT),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.extend([table, Spacer(1, 11)])
        elif line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            text = line[level:].strip()
            anchor = ""
            match = re.match(r"\[(\d+|L1)\]", text)
            if match:
                ident = "local" if match.group(1) == "L1" else match.group(1)
                anchor = f'<a name="ref-{ident}"/>'
            story.append(Paragraph(anchor + inline(text), styles["title" if level == 1 else "h2" if level == 2 else "h3"]))
            idx += 1
        elif line.startswith("> "):
            story.append(Paragraph(inline(line[2:]), styles["quote"]))
            idx += 1
        elif line.startswith("- ") or re.match(r"^\d+\. ", line):
            match = re.match(r"^(\d+\.) ", line)
            marker = match.group(1) if match else "•"
            text = line[match.end():] if match else line[2:]
            story.append(Paragraph(inline(text), styles["bullet"], bulletText=marker))
            idx += 1
        else:
            paragraph = [line]
            idx += 1
            while idx < len(lines) and lines[idx].strip() and not lines[idx].lstrip().startswith(("#", "|", "<!--", "- ", "> ")):
                paragraph.append(lines[idx].strip())
                idx += 1
            text = " ".join(paragraph)
            style = styles["caption"] if re.match(r"^图 \d+\.", text) else body
            story.append(Paragraph(inline(text), style))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(OUTPUT), pagesize=A4, rightMargin=48, leftMargin=48,
                            topMargin=54, bottomMargin=46,
                            title="ResumaCAD 选题报告：几何证据维护与可靠续作",
                            author="ResumaCAD Research", subject="研究定位、相关工作、评测与方法计划")
    doc.build(story, canvasmaker=lambda *a, **kw: NumberedCanvas(*a, font=font, **kw))
    print(OUTPUT.relative_to(ROOT))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", help="Path to a CJK TrueType font; embedded in the PDF")
    build(parser.parse_args().font)

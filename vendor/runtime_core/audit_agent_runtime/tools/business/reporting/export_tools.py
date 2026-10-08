from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from audit_agent_runtime.config.settings import Settings
from audit_agent_runtime.tools.core.base import BaseTool
from audit_agent_runtime.tools.business.reporting.storage import ReportExportMinioService

_ARTIFACT_TYPE = "file"
_FILE_KIND = "report_document"
_TOOL_TYPE = "document_export"
_DEFAULT_EXPORT_SUBDIR = "audit-agent-runtime/report-exports"
_DEFAULT_FORMATS = ["docx"]


@dataclass(slots=True)
class NormalizedReportPayload:
    title: str
    markdown: str


@dataclass(slots=True)
class GeneratedFileDescriptor:
    path: Path
    filename: str
    format: str
    mime_type: str


@dataclass(slots=True)
class ReportGenerationWarning:
    format: str
    code: str
    message: str


@dataclass(slots=True)
class ReportGenerationResult:
    batch_id: str
    report_title: str
    files: list[GeneratedFileDescriptor] = field(default_factory=list)
    warnings: list[ReportGenerationWarning] = field(default_factory=list)


class ReportPayloadError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PandocRenderError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_report_payload(report: Mapping[str, Any]) -> NormalizedReportPayload:
    mode = _coerce_text(report.get("mode")).lower()
    title = _coerce_text(report.get("title"))
    if not title:
        raise ReportPayloadError("invalid_report_payload", "report.title is required")

    if mode == "text":
        text = _coerce_text(report.get("text"))
        if not text:
            raise ReportPayloadError("invalid_report_payload", "report.text is required when mode=text")
        return NormalizedReportPayload(title=title, markdown=text)

    if mode == "structured":
        data = report.get("data")
        if not isinstance(data, Mapping):
            raise ReportPayloadError("invalid_report_payload", "report.data is required when mode=structured")
        markdown = _structured_to_markdown(data)
        if not markdown:
            raise ReportPayloadError("invalid_report_payload", "structured report content is empty")
        return NormalizedReportPayload(title=title, markdown=markdown)

    raise ReportPayloadError("invalid_report_payload", f"unsupported report mode: {mode}")


def _structured_to_markdown(data: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    summary = _coerce_text(data.get("summary"))
    if summary:
        chunks.append(summary)
    sections = data.get("sections")
    if isinstance(sections, Sequence) and not isinstance(sections, (str, bytes)):
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            heading = _coerce_text(section.get("heading"))
            content = _coerce_text(section.get("content"))
            if heading:
                chunks.append(f"## {heading}")
            if content:
                chunks.append(content)
    return "\n\n".join(chunks).strip()


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


class PandocReportRenderer:
    def __init__(
        self,
        *,
        output_dir: Path | str,
        pandoc_bin: str = "pandoc",
        reference_docx: Path | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.pandoc_bin = pandoc_bin
        self.reference_docx = reference_docx

    def generate(self, *, report: Mapping[str, Any], formats: Sequence[str]) -> ReportGenerationResult:
        normalized_formats = self._normalize_formats(formats)
        title = _coerce_text(report.get("title")) or "report"
        markdown = self._extract_markdown(report)
        batch_id = self._generate_batch_id()
        batch_dir = self.output_dir / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)

        source_path = batch_dir / "report.md"
        source_path.write_text(markdown, encoding="utf-8")

        files: list[GeneratedFileDescriptor] = []
        warnings: list[ReportGenerationWarning] = []
        pandoc_path = shutil.which(self.pandoc_bin)
        for fmt in normalized_formats:
            target_path = batch_dir / f"{self._sanitize_file_stem(title)}.{fmt}"
            if fmt == "docx":
                self._render_docx_with_python_docx(title=title, markdown=markdown, target_path=target_path)
                files.append(
                    GeneratedFileDescriptor(
                        path=target_path,
                        filename=target_path.name,
                        format=fmt,
                        mime_type=self._mime_type_for_format(fmt),
                    )
                )
                continue
            if not pandoc_path:
                warnings.append(
                    ReportGenerationWarning(
                        format=fmt,
                        code="pandoc_not_available",
                        message="pandoc binary not found",
                    )
                )
                continue
            cmd = self._build_command(pandoc_path=pandoc_path, source_path=source_path, target_path=target_path, fmt=fmt)
            completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                warnings.append(
                    ReportGenerationWarning(
                        format=fmt,
                        code="pandoc_render_failed",
                        message=(completed.stderr or completed.stdout or "pandoc render failed").strip(),
                    )
                )
                continue
            if not target_path.exists():
                warnings.append(
                    ReportGenerationWarning(
                        format=fmt,
                        code="pandoc_render_failed",
                        message=f"{fmt} output not created",
                    )
                )
                continue
            files.append(
                GeneratedFileDescriptor(
                    path=target_path,
                    filename=target_path.name,
                    format=fmt,
                    mime_type=self._mime_type_for_format(fmt),
                )
            )

        if not files:
            first_warning = warnings[0] if warnings else None
            raise PandocRenderError(
                first_warning.code if first_warning is not None else "pandoc_render_failed",
                first_warning.message if first_warning is not None else "pandoc render failed",
            )
        return ReportGenerationResult(batch_id=batch_id, report_title=title, files=files, warnings=warnings)

    def _generate_without_pandoc(
        self,
        *,
        title: str,
        markdown: str,
        formats: Sequence[str],
        batch_id: str,
        batch_dir: Path,
    ) -> ReportGenerationResult:
        files: list[GeneratedFileDescriptor] = []
        warnings: list[ReportGenerationWarning] = []
        for fmt in formats:
            if fmt != "docx":
                warnings.append(
                    ReportGenerationWarning(
                        format=fmt,
                        code="pandoc_not_available",
                        message="pandoc binary not found",
                    )
                )
                continue

            target_path = batch_dir / f"{self._sanitize_file_stem(title)}.docx"
            self._render_docx_with_python_docx(title=title, markdown=markdown, target_path=target_path)
            files.append(
                GeneratedFileDescriptor(
                    path=target_path,
                    filename=target_path.name,
                    format=fmt,
                    mime_type=self._mime_type_for_format(fmt),
                )
            )

        if not files:
            first_warning = warnings[0] if warnings else None
            raise PandocRenderError(
                "pandoc_not_available",
                first_warning.message if first_warning is not None else "pandoc binary not found",
            )
        return ReportGenerationResult(batch_id=batch_id, report_title=title, files=files, warnings=warnings)

    def _render_docx_with_python_docx(self, *, title: str, markdown: str, target_path: Path) -> None:
        try:
            from docx import Document
            from docx.enum.text import WD_LINE_SPACING
            from docx.oxml.ns import qn
            from docx.shared import Mm, Pt, RGBColor
        except ImportError as exc:  # pragma: no cover
            raise PandocRenderError("docx_renderer_not_available", "python-docx is required for docx export") from exc

        # pandoc 是可选系统依赖；默认 docx 导出使用内置兜底，避免报告主链被本机二进制安装状态阻断。
        document = Document()
        self._configure_docx_document(document, Mm=Mm, Pt=Pt, RGBColor=RGBColor, WD_LINE_SPACING=WD_LINE_SPACING, qn=qn)
        self._add_docx_heading(document, title, level=1, Pt=Pt, RGBColor=RGBColor, qn=qn)
        pending_paragraph: list[str] = []
        lines = self._drop_duplicate_leading_title_heading(markdown, title).splitlines()
        index = 0

        def flush_paragraph() -> None:
            if pending_paragraph:
                paragraph = document.add_paragraph()
                self._add_inline_markdown_runs(
                    paragraph,
                    " ".join(part.strip() for part in pending_paragraph if part.strip()),
                    Pt=Pt,
                    RGBColor=RGBColor,
                    qn=qn,
                )
                pending_paragraph.clear()

        while index < len(lines):
            line = lines[index].rstrip()
            stripped = line.strip()
            if not stripped:
                flush_paragraph()
                index += 1
                continue

            if stripped.startswith("#"):
                flush_paragraph()
                level = min(len(stripped) - len(stripped.lstrip("#")), 4)
                text = stripped[level:].strip()
                if text:
                    self._add_docx_heading(document, text, level=max(1, level), Pt=Pt, RGBColor=RGBColor, qn=qn)
                index += 1
                continue

            if self._is_markdown_table_start(lines, index):
                flush_paragraph()
                index = self._add_markdown_table(document, lines, index)
                continue

            if stripped.startswith(("- ", "* ")):
                flush_paragraph()
                paragraph = document.add_paragraph(style="List Bullet")
                self._add_inline_markdown_runs(paragraph, stripped[2:].strip(), Pt=Pt, RGBColor=RGBColor, qn=qn)
                index += 1
                continue

            numbered = self._strip_numbered_prefix(stripped)
            if numbered is not None:
                flush_paragraph()
                paragraph = document.add_paragraph(style="List Number")
                self._add_inline_markdown_runs(paragraph, numbered, Pt=Pt, RGBColor=RGBColor, qn=qn)
                index += 1
                continue

            pending_paragraph.append(stripped)
            index += 1

        flush_paragraph()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        document.save(target_path)

    def _configure_docx_document(self, document, *, Mm, Pt, RGBColor, WD_LINE_SPACING, qn) -> None:  # noqa: ANN001
        section = document.sections[0]
        section.page_width = Mm(210)
        section.page_height = Mm(297)
        section.top_margin = Mm(25)
        section.bottom_margin = Mm(25)
        section.left_margin = Mm(25)
        section.right_margin = Mm(25)

        normal_style = document.styles["Normal"]
        normal_style.font.name = "Times New Roman"
        normal_style.font.size = Pt(11)
        normal_style.font.color.rgb = RGBColor(0, 0, 0)
        normal_style._element.rPr.rFonts.set(qn("w:eastAsia"), "方正仿宋GB2312")
        normal_paragraph = normal_style.paragraph_format
        normal_paragraph.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
        normal_paragraph.space_before = Pt(0)
        normal_paragraph.space_after = Pt(6)

        for style_name, size in (("Title", 16), ("Heading 1", 16), ("Heading 2", 14), ("Heading 3", 12)):
            style = document.styles[style_name]
            style.font.name = "Times New Roman"
            style.font.size = Pt(size)
            style.font.bold = True
            style.font.color.rgb = RGBColor(0, 0, 0)
            style._element.rPr.rFonts.set(qn("w:eastAsia"), "方正黑体")

    def _add_docx_heading(self, document, text: str, *, level: int, Pt, RGBColor, qn) -> None:  # noqa: ANN001
        paragraph = document.add_heading("", level=min(max(level, 1), 3))
        run = paragraph.add_run(self._strip_inline_markdown(text))
        run.bold = True
        font_size = 16 if level <= 1 else 14 if level == 2 else 12
        self._set_run_font(run, size_pt=font_size, bold=True, Pt=Pt, RGBColor=RGBColor, qn=qn, east_asia="方正黑体")
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(6)

    def _add_inline_markdown_runs(
        self,
        paragraph,
        text: str,
        *,
        Pt,
        RGBColor,
        qn,
        size_pt: float = 11,
        east_asia: str = "方正仿宋GB2312",
    ) -> None:  # noqa: ANN001
        for segment in self._inline_segments(text):
            if segment["kind"] == "break":
                paragraph.add_run().add_break()
                continue
            content = str(segment.get("text", ""))
            if not content:
                continue
            run = paragraph.add_run(content)
            self._set_run_font(
                run,
                size_pt=size_pt,
                bold=bool(segment.get("bold", False)),
                Pt=Pt,
                RGBColor=RGBColor,
                qn=qn,
                east_asia=east_asia,
            )

    def _set_run_font(self, run, *, size_pt: float, bold: bool, Pt, RGBColor, qn, east_asia: str) -> None:  # noqa: ANN001
        run.bold = bold
        run.font.name = "Times New Roman"
        run.font.size = Pt(size_pt)
        run.font.color.rgb = RGBColor(0, 0, 0)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), east_asia)

    def _inline_segments(self, text: str) -> list[dict[str, Any]]:
        normalized = (
            text.replace("<br />", "\n")
            .replace("<br/>", "\n")
            .replace("<br>", "\n")
            .replace("&lt;br /&gt;", "\n")
            .replace("&lt;br/&gt;", "\n")
            .replace("&lt;br&gt;", "\n")
        )
        segments: list[dict[str, Any]] = []
        bold = False
        buffer: list[str] = []
        index = 0
        while index < len(normalized):
            if normalized.startswith("**", index):
                if buffer:
                    segments.append({"kind": "text", "text": "".join(buffer), "bold": bold})
                    buffer.clear()
                bold = not bold
                index += 2
                continue
            char = normalized[index]
            if char == "\n":
                if buffer:
                    segments.append({"kind": "text", "text": "".join(buffer), "bold": bold})
                    buffer.clear()
                segments.append({"kind": "break"})
                index += 1
                continue
            buffer.append(char)
            index += 1
        if buffer:
            segments.append({"kind": "text", "text": "".join(buffer), "bold": bold})
        return segments

    def _strip_inline_markdown(self, text: str) -> str:
        return (
            text.replace("**", "")
            .replace("<br />", " ")
            .replace("<br/>", " ")
            .replace("<br>", " ")
            .replace("&lt;br /&gt;", " ")
            .replace("&lt;br/&gt;", " ")
            .replace("&lt;br&gt;", " ")
        ).strip()

    def _drop_duplicate_leading_title_heading(self, markdown: str, title: str) -> str:
        lines = markdown.splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            heading_text = self._markdown_heading_text(stripped)
            if heading_text and self._canonical_heading_text(heading_text) == self._canonical_heading_text(title):
                return "\n".join(lines[:index] + lines[index + 1 :])
            return markdown
        return markdown

    def _markdown_heading_text(self, line: str) -> str:
        if not line.startswith("#"):
            return ""
        level = len(line) - len(line.lstrip("#"))
        if level <= 0 or level > 6:
            return ""
        text = line[level:].strip()
        return self._strip_inline_markdown(text)

    def _canonical_heading_text(self, text: str) -> str:
        return "".join(self._strip_inline_markdown(text).split()).lower()

    def _is_markdown_table_start(self, lines: Sequence[str], index: int) -> bool:
        if index + 1 >= len(lines):
            return False
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        return "|" in header and "|" in separator and set(separator.replace("|", "").replace(":", "").replace("-", "").strip()) == set()

    def _add_markdown_table(self, document, lines: Sequence[str], index: int) -> int:  # noqa: ANN001
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Pt, RGBColor

        rows: list[list[str]] = []
        while index < len(lines):
            stripped = lines[index].strip()
            if "|" not in stripped:
                break
            if set(stripped.replace("|", "").replace(":", "").replace("-", "").strip()) == set():
                index += 1
                continue
            rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
            index += 1
        if not rows:
            return index
        table = document.add_table(rows=len(rows), cols=max(len(row) for row in rows))
        table.style = "Table Grid"
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                cell = table.rows[row_index].cells[column_index]
                self._set_docx_cell_text(cell, value, Pt=Pt, RGBColor=RGBColor, qn=qn, size_pt=10.5)
                if row_index == 0:
                    self._shade_docx_cell(cell, fill="F2F2F2", OxmlElement=OxmlElement, qn=qn)
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.bold = True
        return index

    def _set_docx_cell_text(self, cell, text: str, *, Pt, RGBColor, qn, size_pt: float) -> None:  # noqa: ANN001
        cell.text = ""
        paragraph = cell.paragraphs[0]
        self._add_inline_markdown_runs(paragraph, text, Pt=Pt, RGBColor=RGBColor, qn=qn, size_pt=size_pt)

    def _shade_docx_cell(self, cell, *, fill: str, OxmlElement, qn) -> None:  # noqa: ANN001
        tc_pr = cell._tc.get_or_add_tcPr()
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), fill)
        tc_pr.append(shading)

    def _strip_numbered_prefix(self, value: str) -> str | None:
        dot_index = value.find(".")
        if dot_index <= 0:
            return None
        prefix = value[:dot_index]
        if not prefix.isdigit():
            return None
        text = value[dot_index + 1 :].strip()
        return text or None

    def _build_command(self, *, pandoc_path: str, source_path: Path, target_path: Path, fmt: str) -> list[str]:
        cmd = [pandoc_path, str(source_path), "-o", str(target_path)]
        if fmt == "docx" and self.reference_docx is not None and self.reference_docx.exists():
            cmd.extend(["--reference-doc", str(self.reference_docx)])
        if fmt == "pdf":
            cmd.append("--pdf-engine=xelatex")
        return cmd

    def _extract_markdown(self, report: Mapping[str, Any]) -> str:
        mode = _coerce_text(report.get("mode")).lower()
        if mode == "text":
            return _coerce_text(report.get("text"))
        if mode == "structured":
            data = report.get("data")
            if not isinstance(data, Mapping):
                return ""
            return _structured_to_markdown(data)
        raise PandocRenderError("invalid_report_payload", f"unsupported report mode: {mode}")

    def _normalize_formats(self, formats: Sequence[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for fmt in formats:
            normalized = _coerce_text(fmt).lower()
            if not normalized or normalized in seen:
                continue
            if normalized not in {"docx", "pdf"}:
                raise PandocRenderError("unsupported_format", f"unsupported format: {normalized}")
            seen.add(normalized)
            cleaned.append(normalized)
        return cleaned or ["docx"]

    def _generate_batch_id(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"report_{now}_{uuid4().hex[:8]}"

    def _sanitize_file_stem(self, title: str) -> str:
        stem = "".join("_" if ch in "\\/:*?\"<>|" else ch for ch in title).strip()
        stem = "_".join(stem.split())
        return stem or "report"

    def _mime_type_for_format(self, fmt: str) -> str:
        if fmt == "pdf":
            return "application/pdf"
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _default_output_dir() -> Path:
    return Path(tempfile.gettempdir()) / _DEFAULT_EXPORT_SUBDIR


def _normalize_export_formats(formats: list[str] | None) -> list[str]:
    cleaned = [fmt.strip() for fmt in (formats or []) if fmt and fmt.strip()]
    return cleaned or list(_DEFAULT_FORMATS)


def _artifact_to_dict(artifact) -> dict[str, Any]:
    return {
        "name": artifact.name,
        "url": artifact.url,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "expires_at": artifact.expires_at.isoformat() if artifact.expires_at else None,
    }


def _build_message(report_title: str, files_count: int, warnings_count: int) -> str:
    message = f"已导出 {files_count} 个报告文件：{report_title}"
    if warnings_count:
        message += f"，其中 {warnings_count} 个格式导出失败"
    return message


def _export_report_document_impl(
    *,
    report: dict[str, Any],
    formats: list[str] | None = None,
    settings: Settings | None = None,
    document_service=None,
    minio_service=None,
    artifact_metadata: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or Settings()
    normalized_formats = _normalize_export_formats(formats)
    normalized_report = normalize_report_payload(report)
    if document_service is None:
        document_service = PandocReportRenderer(output_dir=_default_output_dir())
    if minio_service is None:
        minio_service = ReportExportMinioService(settings=resolved_settings)
    doc_service = document_service
    storage_service = minio_service
    generated = doc_service.generate(
        report={
            "mode": "text",
            "title": normalized_report.title,
            "text": normalized_report.markdown,
        },
        formats=normalized_formats,
    )
    generated_at = datetime.now(timezone.utc)
    files: list[dict[str, Any]] = []
    for file_desc in generated.files:
        upload_metadata = {
            "artifact_type": _ARTIFACT_TYPE,
            "file_kind": _FILE_KIND,
            "batch_id": generated.batch_id,
            **dict(artifact_metadata or {}),
        }
        upload_result = storage_service.upload(
            file_path=file_desc.path,
            export_id=generated.batch_id,
            generated_at=generated_at,
            content_type=file_desc.mime_type,
            metadata=upload_metadata,
        )
        artifact = storage_service.presign(upload_result)
        files.append(_artifact_to_dict(artifact))
    return {
        "ok": True,
        "tool_type": _TOOL_TYPE,
        "artifact_type": _ARTIFACT_TYPE,
        "file_kind": _FILE_KIND,
        "report_title": generated.report_title,
        "files": files,
        "message": _build_message(generated.report_title, len(files), len(generated.warnings)),
    }


class ExportReportDocumentTool(BaseTool):
    name = "export_report_document"
    description = "将已经整理完成的正式报告导出为 docx/pdf，并返回前端可渲染的文件附件。优先消费当前会话中已有的模型结果、详情下钻结果和主智能体总结来组织 report 内容。调用时必须一次性提供 report.mode、report.title、report.text；不要用空参数或占位参数试探调用。自然语言回复只需说明报告已生成，不要输出文件链接。"
    input_schema = {
        "type": "object",
        "properties": {
            "report": {
                "type": "object",
                "description": "报告内容。必须使用 {mode:'text', title:'标题', text:'完整Markdown正文'}；text 必须是已经整理好的 Markdown 正文。",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["text", "structured"],
                        "description": "报告内容模式。优先使用 text。",
                    },
                    "title": {
                        "type": "string",
                        "description": "报告标题，例如 国家管网集团2025年度串围标风险分析报告。",
                    },
                    "text": {
                        "type": "string",
                        "description": "mode=text 时必填。完整 Markdown 正文，不要为空，不要使用占位符。",
                    },
                    "data": {
                        "type": "object",
                        "description": "mode=structured 时必填的结构化报告数据。",
                    },
                },
                "required": ["mode", "title"],
                "additionalProperties": True,
            },
            "formats": {
                "type": "array",
                "items": {"type": "string", "enum": ["docx", "pdf"]},
                "description": "导出格式列表，支持 docx 和 pdf，默认仅生成 docx。",
            },
        },
        "required": ["report"],
    }

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()

    def execute(self, arguments: dict, context) -> dict:
        report = arguments.get("report") or {}
        formats = arguments.get("formats")
        overrides = context if isinstance(context, dict) else {}
        session_id = (
            overrides.get("session_id")
            if isinstance(context, dict)
            else getattr(context, "session_id", None)
        )
        return _export_report_document_impl(
            report=report,
            formats=formats,
            settings=self.settings,
            document_service=overrides.get("document_service"),
            minio_service=overrides.get("minio_service"),
            artifact_metadata=(
                {"owner_session_id": str(session_id)}
                if session_id
                else None
            ),
        )


export_report_document = ExportReportDocumentTool()


def build_report_export_tools(settings: Settings | None = None) -> list[BaseTool]:
    return [ExportReportDocumentTool(settings=settings)]


__all__ = [
    "ExportReportDocumentTool",
    "PandocRenderError",
    "PandocReportRenderer",
    "ReportPayloadError",
    "_export_report_document_impl",
    "build_report_export_tools",
    "export_report_document",
    "normalize_report_payload",
]

from __future__ import annotations

from dataclasses import dataclass

CLEARED_OLD_TOOL_RESULT_CONTENT = "[Old tool result content cleared]"


@dataclass(frozen=True, slots=True)
class ToolResultClearRequest:
    tool_use_id: str
    tool_name: str
    content: object


class ToolResultPolicyRegistry:
    """模型上下文里的 tool_result 治理策略入口。

    这里集中维护“哪些结果可折叠、哪些旧结果可清理、清理后保留哪些续查字段”。
    运行时预算算法只负责选择时机和数量，业务 envelope 语义放在策略层。
    """

    def should_collapse_for_budget(self, *, tool_name: str, content: dict) -> bool:
        return not (
            self.is_error_result(content)
            or self.is_document_export_result(tool_name=tool_name, content=content)
        )

    def build_old_result_replacement(
        self,
        *,
        tool_use_id: str,
        tool_name: str,
        content: object,
    ) -> dict | str | None:
        if not isinstance(content, dict):
            return CLEARED_OLD_TOOL_RESULT_CONTENT
        if self.is_error_result(content):
            return None
        if self.is_document_export_result(tool_name=tool_name, content=content):
            return None
        if self.is_table_dataset_result(content):
            return self._build_cleared_table_result(
                ToolResultClearRequest(
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    content=content,
                )
            )
        return CLEARED_OLD_TOOL_RESULT_CONTENT

    def is_error_result(self, content: dict) -> bool:
        return content.get("is_error") is True or content.get("ok") is False

    def is_document_export_result(self, *, tool_name: str, content: dict) -> bool:
        return (
            tool_name == "export_report_document"
            or content.get("tool_type") == "document_export"
            or content.get("artifact_type") == "report_document"
        )

    def is_table_dataset_result(self, content: dict) -> bool:
        return (
            content.get("tool_type") == "table_query"
            or content.get("artifact_type") == "model_result_dataset"
            or content.get("type") == "model_result_dataset"
        )

    def _build_cleared_table_result(self, request: ToolResultClearRequest) -> dict:
        assert isinstance(request.content, dict)
        # 表格结果清理不能只给占位符：模型后续可能需要用 query_handle 继续翻页，
        # 所以保留证据包 envelope 的轻量字段，明确去掉 rows 这类大 payload。
        cleared = {
            "cleared": True,
            "clear_reason": "old_tool_result",
            "tool_name": request.tool_name,
            "tool_use_id": request.tool_use_id,
        }
        for key in (
            "ok",
            "type",
            "artifact_type",
            "tool_type",
            "dataset_title",
            "schema",
            "summary",
            "query_handle",
            "page",
            "page_size",
            "has_more",
            "row_count",
            "next_action",
            "next_tool",
            "match_type",
            "need_confirmation",
            "matched_item",
            "candidates",
        ):
            if key in request.content:
                cleared[key] = request.content[key]
        return cleared


DEFAULT_TOOL_RESULT_POLICY_REGISTRY = ToolResultPolicyRegistry()

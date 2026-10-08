from __future__ import annotations

import logging
import json
import re
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from audit_agent_runtime.config.settings import Settings
from audit_agent_runtime.persistence.db import build_engine
from audit_agent_runtime.tools.core.base import BaseTool
from audit_agent_runtime.tools.business.procurement.bid_collusion.dimensions import (
    BID_COLLUSION_DETAIL_MODEL_ID,
    BidCollusionDimensionSpec,
    allowed_ydlx_values,
    dimension_to_dict,
    get_dimension_by_model_name,
    get_dimension_by_scene_field,
    get_dimension_by_single_model_id,
    get_dimension_by_ydlx,
    scene_dimension_fields,
)
from audit_agent_runtime.tools.business.procurement.shared.data_access import (
    as_rows as _as_rows,
    default_connection_factory as _default_connection_factory,
    fetch_one_row as _fetch_one_row,
    fetch_rows as _fetch_rows,
)
from audit_agent_runtime.tools.business.procurement.shared.sql_logging import (
    format_sql_params_for_log as _format_sql_params_for_log,
    log_sql_execution as _log_sql_execution,
    render_runnable_sql_for_log as _render_runnable_sql_for_log,
    sql_literal_for_log as _sql_literal_for_log,
    strip_sql_semicolon as _strip_sql_semicolon,
    truncate_log_text as _truncate_log_text,
)
from audit_agent_runtime.tools.business.procurement.shared.workflow_cache import (
    cache_validator_result as _cache_validator_result,
    load_cached_validator_result as _load_cached_validator_result,
    load_workflow_state as _load_workflow_state,
    save_workflow_state as _save_workflow_state,
    session_cache_key as _session_cache_key,
)


logger = logging.getLogger(__name__)

MODEL_CATALOG_TABLE = "rst_sj_dzzb_model_tag"
MODEL_PARAMETER_TABLE = "rst_sj_dzzb_model_params"
MODEL_SQL_TABLE = "rst_sj_dzzb_model_sql"
MODEL_RESULT_DATASET_TYPE = "model_result_dataset"
BID_COLLUSION_SCENE_MODEL_ID = "bid_collusion_scene_screening"
BID_COLLUSION_DETAIL_MODEL_NAME = "串围标详情下钻"
PERSON_REPEAT_FIELD = "人员关联次数"
TRUNCATED_PERSON_REPEAT_FIELD = "不同投标人的投标联系人、授权代表人、项目负"
FULL_PERSON_REPEAT_FIELD = "不同投标人的投标联系人、授权代表人、项目负责人相同"
DETAIL_REPORT_ROW_LIMIT = 10


class QueryModelDataTool(BaseTool):
    name = "query_model_data"
    description = "查询普通采购分析场景下的模型清单；支持按模型名称、风险标签精确优先和模糊兜底查询，也支持显式返回模型清单。若问题已经命中串围标专项主链，不要把它当成首轮入口，应改用专项 skill 与 advance_model_workflow。"
    input_schema = {
        "type": "object",
        "properties": {
            "model_name": {
                "type": "string",
                "description": "模型名称，例如 不同投标人电话、邮箱相同。只填写真实模型名称，不要填写查询语句。",
            },
            "risk_tag": {
                "type": "string",
                "description": "风险标签，例如 串围标；可单独筛选，也可与模型名称联合筛选",
            },
            "list_all": {
                "type": "boolean",
                "description": "是否返回模型清单；用户想查看全部模型或模型列表时传 true",
            },
            "limit": {"type": "integer", "description": "返回数量上限，默认 100"},
        },
        "required": [],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        query_text = _normalize_model_phrase(str(arguments.get("model_name", "")).strip())
        risk_tag = _normalize_model_phrase(str(arguments.get("risk_tag", "")).strip())
        list_all = bool(arguments.get("list_all"))
        limit = _coerce_limit(arguments.get("limit"), default=100)
        legacy_listing = _is_select_all_query(query_text)
        is_listing = list_all or legacy_listing
        if legacy_listing:
            query_text = ""
        if not is_listing and not query_text and not risk_tag:
            return {
                "ok": False,
                "query_text": "",
                "risk_tag": "",
                "match_type": "invalid_arguments",
                "need_confirmation": False,
                "matched_item": None,
                "candidates": [],
                "items": [],
                "total_returned": 0,
                "summary": "请至少提供 model_name、risk_tag，或使用 list_all=true 查询模型清单。",
            }

        if is_listing and not risk_tag:
            sql = """
SELECT
    model_name,
    risk_tag
FROM public.rst_sj_dzzb_model_tag
ORDER BY risk_tag NULLS LAST, model_name
LIMIT %s
""".strip()
            rows = self._run_query(sql, [limit])
            items = [_map_model_catalog_row(row) for row in rows]
            return {
                "ok": True,
                "query_text": query_text,
                "risk_tag": risk_tag,
                "match_type": "all",
                "need_confirmation": False,
                "matched_item": None,
                "candidates": [],
                "items": items,
                "total_returned": len(items),
                "summary": f"已返回 {len(items)} 条模型清单记录。",
            }

        if query_text and not is_listing and risk_tag:
            exact_sql = """
SELECT
    model_name,
    risk_tag
FROM public.rst_sj_dzzb_model_tag
WHERE model_name = %s AND risk_tag = %s
ORDER BY risk_tag NULLS LAST, model_name
LIMIT %s
""".strip()
            exact_rows = self._run_query(exact_sql, [query_text, risk_tag, 10])
            if exact_rows:
                return _model_catalog_exact_result(
                    query_text=query_text,
                    risk_tag=risk_tag,
                    row=exact_rows[0],
                    summary_prefix="已按模型名称和风险标签精确匹配到 1 条模型清单记录",
                )
            terms = _model_search_terms(query_text)
            model_clause = " OR ".join(["model_name ILIKE %s" for _ in terms])
            fuzzy_sql = f"""
SELECT
    model_name,
    risk_tag
FROM public.rst_sj_dzzb_model_tag
WHERE ({model_clause}) AND risk_tag ILIKE %s
ORDER BY risk_tag NULLS LAST, model_name
LIMIT %s
""".strip()
            rows = self._run_query(
                fuzzy_sql,
                [*[f"%{term}%" for term in terms], f"%{risk_tag}%", min(limit, 100)],
            )
            return _model_catalog_candidates_result(
                query_text=query_text,
                risk_tag=risk_tag,
                rows=rows,
                none_summary=f"未在模型清单中找到模型名称匹配“{query_text}”且风险标签匹配“{risk_tag}”的记录。",
            )

        if query_text and not is_listing:
            exact_sql = """
SELECT
    model_name,
    risk_tag
FROM public.rst_sj_dzzb_model_tag
WHERE model_name = %s
ORDER BY risk_tag NULLS LAST, model_name
LIMIT %s
""".strip()
            exact_rows = self._run_query(exact_sql, [query_text, 10])
            if exact_rows:
                return _model_catalog_exact_result(
                    query_text=query_text,
                    risk_tag=risk_tag,
                    row=exact_rows[0],
                    summary_prefix="已精确匹配到 1 条模型清单记录",
                )
            terms = _model_search_terms(query_text)
            where_clause = " OR ".join(["model_name ILIKE %s" for _ in terms])
            fuzzy_sql = f"""
SELECT
    model_name,
    risk_tag
FROM public.rst_sj_dzzb_model_tag
WHERE {where_clause}
ORDER BY risk_tag NULLS LAST, model_name
LIMIT %s
""".strip()
            rows = self._run_query(
                fuzzy_sql,
                [*[f"%{term}%" for term in terms], min(limit, 100)],
            )
            return _model_catalog_candidates_result(
                query_text=query_text,
                risk_tag=risk_tag,
                rows=rows,
                none_summary=f"未在模型清单中找到与“{query_text}”匹配的记录。",
            )

        exact_sql = """
SELECT
    model_name,
    risk_tag
FROM public.rst_sj_dzzb_model_tag
WHERE risk_tag = %s
ORDER BY risk_tag NULLS LAST, model_name
LIMIT %s
""".strip()
        exact_rows = self._run_query(exact_sql, [risk_tag, limit])
        if exact_rows:
            return _model_catalog_items_result(
                query_text=query_text,
                risk_tag=risk_tag,
                rows=exact_rows,
                match_type="exact",
                summary=f"已按风险标签精确匹配到 {len(exact_rows)} 条模型清单记录。",
            )
        fuzzy_sql = """
SELECT
    model_name,
    risk_tag
FROM public.rst_sj_dzzb_model_tag
WHERE risk_tag ILIKE %s
ORDER BY risk_tag NULLS LAST, model_name
LIMIT %s
""".strip()
        fuzzy_rows = self._run_query(fuzzy_sql, [f"%{risk_tag}%", limit])
        return _model_catalog_items_result(
            query_text=query_text,
            risk_tag=risk_tag,
            rows=fuzzy_rows,
            match_type="fuzzy" if fuzzy_rows else "none",
            summary=(
                f"未精确匹配到风险标签，按模糊匹配返回 {len(fuzzy_rows)} 条模型清单记录。"
                if fuzzy_rows
                else f"未在模型清单中找到风险标签匹配“{risk_tag}”的记录。"
            ),
        )

    def _run_query(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        conn = self.connection_factory()
        try:
            cur = conn.cursor()
            try:
                _log_sql_execution(f"{self.name}._run_query", sql, params)
                cur.execute(sql, params)
                records = cur.fetchall()
                columns = [item[0] for item in cur.description]
            finally:
                cur.close()
        finally:
            conn.close()
        return _as_rows(columns, records)


class QueryGroupUnitMasterTool(BaseTool):
    name = "query_group_unit_master"
    description = "查询集团单位主数据；可用 company_name 校验集团内部单位名称，也可用 list_all=true 返回全量单位编码和名称清单。仅在 advance_model_workflow 明确要求校验主体参数时调用校验模式；不要把校验模式当成通用前置探测。"
    input_schema = {
        "type": "object",
        "properties": {
            "company_name": {
                "type": "string",
                "description": "待确认的集团内部单位名称或简称，例如 华东分公司",
            },
            "list_all": {
                "type": "boolean",
                "description": "是否查询集团单位主数据全量清单；为 true 时不需要 company_name，仅返回单位编码和单位名称。",
            },
        },
        "required": [],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        query_handle = _coerce_mapping(arguments.get("query_handle"))
        if bool(arguments.get("list_all")) or query_handle.get("kind") == "group_unit_master":
            return self._execute_list_query(arguments, query_handle=query_handle)

        query_text = _normalize_org_phrase(str(arguments.get("company_name", "")).strip())
        if not query_text:
            return {
                "ok": False,
                "query_text": "",
                "match_type": "invalid_arguments",
                "need_confirmation": False,
                "matched_item": None,
                "candidates": [],
                "summary": "请提供 company_name，或使用 list_all=true 查询集团单位主数据清单。",
            }

        exact_sql = """
SELECT
    u.unit_id,
    u.unit_code,
    u.unit_name,
    u.unit_short_name,
    u.org_type,
    p.unit_name AS parent_unit_name,
    u.unit_status
FROM public.md_group_unit u
LEFT JOIN public.md_group_unit p ON p.unit_id = u.parent_unit_id
WHERE u.unit_name = %s OR u.unit_short_name = %s
ORDER BY u.unit_id
LIMIT 10
""".strip()
        exact_rows = self._run_query(exact_sql, [query_text, query_text])
        if exact_rows:
            matched = _map_group_unit_row(exact_rows[0])
            result = {
                "ok": True,
                **_build_group_unit_dataset_payload(
                    rows=[_group_unit_table_row(matched)],
                    query_text=query_text,
                    match_type="exact",
                ),
                "query_text": query_text,
                "match_type": "exact",
                "need_confirmation": False,
                "matched_item": matched,
                "candidates": [],
                "summary": f"已精确匹配到 1 条单位主数据记录：{matched['name']}。",
            }
            _cache_validator_result(context, self.name, query_text, result)
            return result

        candidate_sql = """
SELECT
    u.unit_id,
    u.unit_code,
    u.unit_name,
    u.unit_short_name,
    u.org_type,
    p.unit_name AS parent_unit_name,
    u.unit_status
FROM public.md_group_unit u
LEFT JOIN public.md_group_unit p ON p.unit_id = u.parent_unit_id
WHERE u.unit_name ILIKE %s OR u.unit_short_name ILIKE %s
ORDER BY u.unit_name
LIMIT 10
""".strip()
        candidate_rows = self._run_query(candidate_sql, [f"%{query_text}%", f"%{query_text}%"])
        candidates = [_map_group_unit_row(row) for row in candidate_rows]
        if not candidates:
            available_units = self._list_group_unit_items()
            return {
                "ok": True,
                **_build_group_unit_dataset_payload(
                    rows=available_units,
                    query_text=query_text,
                    match_type="none",
                    page_size=len(available_units),
                ),
                "query_text": query_text,
                "match_type": "none",
                "need_confirmation": bool(available_units),
                "matched_item": None,
                "candidates": available_units,
                "items": available_units,
                "total_returned": len(available_units),
                "summary": (
                    f"未在集团单位主数据中找到与“{query_text}”匹配的记录，"
                    f"已返回 {len(available_units)} 个可选主体供确认。"
                ),
            }
        return {
            "ok": True,
            **_build_group_unit_dataset_payload(
                rows=[_group_unit_table_row(candidate) for candidate in candidates],
                query_text=query_text,
                match_type="candidate" if candidates else "none",
            ),
            "query_text": query_text,
            "match_type": "candidate" if candidates else "none",
            "need_confirmation": bool(candidates),
            "matched_item": None,
            "candidates": candidates,
            "summary": (
                f"未精确匹配到单位主数据，返回 {len(candidates)} 个候选项供确认。"
                if candidates
                else f"未在集团单位主数据中找到与“{query_text}”匹配的记录。"
            ),
        }

    def _execute_list_query(self, arguments: dict, *, query_handle: dict[str, Any]) -> dict:
        query_text = str(query_handle.get("query_text") or "").strip()
        items = self._list_group_unit_items()
        filters = _coerce_mapping(arguments.get("filters"))
        filtered_items = _filter_group_unit_items(items, filters)
        page = max(1, int(arguments.get("page") or 1))
        explicit_page_size = arguments.get("page_size") or arguments.get("limit")
        page_size = (
            _coerce_limit(explicit_page_size, default=20)
            if explicit_page_size is not None
            else max(len(filtered_items), 1)
        )
        start = (page - 1) * page_size
        visible_items = filtered_items[start : start + page_size]
        return {
            "ok": True,
            **_build_group_unit_dataset_payload(
                rows=visible_items,
                query_text=query_text,
                match_type="list_all",
                page_size=page_size,
            ),
            "query_text": query_text,
            "match_type": "list_all",
            "need_confirmation": False,
            "matched_item": None,
            "candidates": [],
            "items": visible_items,
            "total_returned": len(visible_items),
            "filters": filters,
            "has_more": start + page_size < len(filtered_items),
            "summary": f"已返回 {len(visible_items)} 条集团单位主数据记录。",
        }

    def _list_group_unit_items(self) -> list[dict[str, Any]]:
        list_sql = """
SELECT
    u.unit_code,
    u.unit_name
FROM public.md_group_unit u
ORDER BY u.unit_code, u.unit_name
""".strip()
        rows = self._run_query(list_sql, [])
        return [
            {
                "code": row.get("unit_code"),
                "name": row.get("unit_name"),
            }
            for row in rows
        ]

    def _run_query(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        conn = self.connection_factory()
        try:
            cur = conn.cursor()
            try:
                _log_sql_execution(f"{self.name}._run_query", sql, params)
                cur.execute(sql, params)
                records = cur.fetchall()
                columns = [item[0] for item in cur.description]
            finally:
                cur.close()
        finally:
            conn.close()
        return _as_rows(columns, records)


class QueryModelCatalogByRiskTool(BaseTool):
    name = "query_model_catalog_by_risk"
    description = (
        "按风险标签和目录项类型查询模型目录清单。当用户询问有哪些监督模型、单一模型、场景模型或中间表清单时使用；"
        "不要用于执行模型、查询模型结果、查看中间表字段或生成 SQL。目录表通过 model_type 区分单一模型、场景模型和中间表；"
        "不要用于 Ontology 治理资产问数；用户询问风险指标、语义指标、阈值规则、动作策略、通知策略、模型结果映射、审计发现、资源结果字段或实体穿透关系时，应使用 resolve_enterprise_ontology。"
        "查询监督模型清单时 model_types 传 ['单一模型','场景模型']，只查单一模型传 ['单一模型']，"
        "只查场景模型传 ['场景模型']，查中间表清单传 ['中间表']。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "risk_tag": {"type": "string", "description": "风险标签，例如 串围标"},
            "model_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "目录项类型过滤，取值来自模型目录表 model_type。",
            },
            "limit": {"type": "integer", "description": "返回数量上限，默认 100"},
        },
        "required": ["risk_tag"],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        risk_tag = _normalize_model_phrase(str(arguments.get("risk_tag", "")).strip())
        model_types = _coerce_model_type_filter(arguments.get("model_types"))
        type_filter_sql, type_filter_params = _model_type_filter_clause(model_types)
        limit = _coerce_limit(arguments.get("limit"), default=100)
        if not risk_tag:
            return {
                "ok": False,
                "risk_tag": "",
                "match_type": "invalid_arguments",
                "items": [],
                "total_returned": 0,
                "summary": "请提供 risk_tag。",
            }

        exact_sql = f"""
SELECT
    id,
    model_id,
    model_name,
    risk_tag,
    model_type,
    result_table,
    executor_code
FROM public.{MODEL_CATALOG_TABLE}
WHERE risk_tag = %s
{type_filter_sql}
ORDER BY model_type NULLS LAST, model_name
LIMIT %s
""".strip()
        exact_rows = _fetch_rows(
            self.connection_factory,
            exact_sql,
            [risk_tag, *type_filter_params, limit],
        )
        if exact_rows:
            items = [_map_model_catalog_row(row) for row in exact_rows]
            return {
                "ok": True,
                "risk_tag": risk_tag,
                "model_types": model_types,
                "match_type": "exact",
                "items": items,
                "total_returned": len(items),
                "summary": f"已按风险标签精确匹配到 {len(items)} 个模型。",
            }

        fuzzy_sql = f"""
SELECT
    id,
    model_id,
    model_name,
    risk_tag,
    model_type,
    result_table,
    executor_code
FROM public.{MODEL_CATALOG_TABLE}
WHERE risk_tag ILIKE %s
{type_filter_sql}
ORDER BY model_type NULLS LAST, model_name
LIMIT %s
""".strip()
        fuzzy_rows = _fetch_rows(
            self.connection_factory,
            fuzzy_sql,
            [f"%{risk_tag}%", *type_filter_params, limit],
        )
        items = [_map_model_catalog_row(row) for row in fuzzy_rows]
        return {
            "ok": True,
            "risk_tag": risk_tag,
            "model_types": model_types,
            "match_type": "fuzzy" if items else "none",
            "items": items,
            "total_returned": len(items),
            "summary": (
                f"未精确匹配到风险标签，按模糊匹配返回 {len(items)} 个模型。"
                if items
                else f"未找到风险标签为“{risk_tag}”的模型。"
            ),
        }


class QueryModelParametersTool(BaseTool):
    name = "query_model_parameters"
    description = "内部辅助工具：查询模型执行所需参数定义。默认由 advance_model_workflow 在内部调度，主智能体应直接读取它返回的参数 schema。"
    input_schema = {
        "type": "object",
        "properties": {
            "model_name": {"type": "string", "description": "模型名称"},
        },
        "required": ["model_name"],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        model_name = str(arguments.get("model_name", "")).strip()
        sql = f"""
SELECT
    model_name,
    param_name,
    required,
    display_order,
    validator_tool
FROM public.{MODEL_PARAMETER_TABLE}
WHERE model_name = %s
ORDER BY display_order ASC, param_name ASC
""".strip()
        rows = _fetch_rows(self.connection_factory, sql, [model_name])
        items = [_map_model_parameter_row(row) for row in rows]
        items.sort(
            key=lambda item: (
                item.get("display_order") if isinstance(item.get("display_order"), int) else 10**9,
                str(item.get("param_name") or ""),
            )
        )
        return {
            "ok": True,
            "model_name": model_name,
            "items": items,
            "total_returned": len(items),
            "summary": (
                f"已读取模型“{model_name}”的 {len(items)} 个参数定义。"
                if items
                else f"未找到模型“{model_name}”的参数定义。"
            ),
        }


class PlanModelExecutionTool(BaseTool):
    name = "plan_model_execution"
    description = "内部辅助工具：根据模型参数定义生成最小澄清结果。默认由 advance_model_workflow 在内部复用，主智能体不要直接把它当成规划入口。"
    input_schema = {
        "type": "object",
        "properties": {
            "model_name": {"type": "string", "description": "模型名称"},
            "user_query": {"type": "string", "description": "用户当前问题"},
            "history_summary": {"type": "string", "description": "历史摘要"},
            "confirmed_params": {"type": "object", "description": "已确认参数"},
            "candidate_params": {"type": "object", "description": "候选参数"},
            "validation_results": {"type": "object", "description": "参数校验结果"},
        },
        "required": ["model_name", "user_query"],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        model_name = str(arguments.get("model_name", "")).strip()
        user_query = str(arguments.get("user_query", "")).strip()
        history_summary = str(arguments.get("history_summary", "")).strip()
        confirmed_params = _coerce_mapping(arguments.get("confirmed_params"))
        candidate_params = _coerce_mapping(arguments.get("candidate_params"))
        validation_results = _coerce_mapping(arguments.get("validation_results"))
        parameter_defs = QueryModelParametersTool(
            connection_factory=self.connection_factory
        ).execute({"model_name": model_name}, context=context)
        items = parameter_defs.get("items", [])
        combined = _combined_text(
            _remove_model_mentions(user_query, [model_name]),
            history_summary,
        )

        extracted_params: dict[str, Any] = {}
        missing_required_params: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            param_name = str(item.get("param_name", "")).strip()
            if not param_name:
                continue
            value = _resolve_model_param_value(
                param_name=param_name,
                combined_text=combined,
                confirmed_params=confirmed_params,
                candidate_params=candidate_params,
                validation_results=validation_results,
                validator_tool=str(item.get("validator_tool") or "").strip(),
            )
            extracted_params[param_name] = value
            validation = validation_results.get(param_name)
            if isinstance(validation, dict) and validation.get("ok") is False:
                missing_required_params.append(
                    {
                        "param_name": param_name,
                        "reason": str(validation.get("reason") or f"参数 {param_name} 未通过校验"),
                    }
                )
                continue
            if bool(item.get("required")) and value in ("", None):
                missing_required_params.append(
                    {
                        "param_name": param_name,
                        "reason": f"模型执行缺少必填参数 {param_name}",
                    }
                )

        strict_message = _build_model_param_clarification_message(missing_required_params)
        return {
            "ok": True,
            "model_name": model_name,
            "parameter_items": items,
            "extracted_params": extracted_params,
            "missing_required_params": missing_required_params,
            "strict_clarification_message": strict_message,
            "can_execute": not missing_required_params,
            "summary": (
                f"模型“{model_name}”的参数已补齐，可以执行。"
                if not missing_required_params
                else f"模型“{model_name}”仍缺少 {len(missing_required_params)} 个必填参数。"
            ),
        }


class AdvanceModelWorkflowTool(BaseTool):
    name = "advance_model_workflow"
    description = (
        "串围标专项 skill 内部规划入口。只有当前轮已经加载并进入 bid_collusion_planning skill，"
        "且用户明确要执行串围标模型筛查、模型流程推进、参数确认、执行确认或报告生成时才可调用。"
        "普通审计问数、查看中间表数据、查询有哪些结果、明细列表、统计、实体关系或生成 SQL 时不要调用本工具；"
        "这些 NLP2SQL 问数必须优先调用 resolve_enterprise_ontology，再调用 validate_enterprise_sql。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "user_query": {"type": "string", "description": "用户当前输入，仅用于给返回结果补充上下文，不用于参数抽取"},
            "risk_tag": {"type": "string", "description": "风险标签；当前专项可直接传 串围标"},
            "model_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "目录项类型过滤，取值来自模型目录表 model_type。",
            },
            "model_name": {"type": "string", "description": "已选模型名称"},
            "confirmed_params": {"type": "object", "description": "模型已根据上下文确认的参数；tool 不会自行抽取"},
            "validation_results": {"type": "object", "description": "参数校验结果"},
            "execution_confirmed": {"type": "boolean", "description": "用户是否已明确确认执行"},
            "history_summary": {"type": "string", "description": "历史摘要"},
        },
        "required": ["user_query"],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        user_query = str(arguments.get("user_query", "")).strip()
        history_summary = str(arguments.get("history_summary", "")).strip()
        cached_state = _load_workflow_state(context)
        cached_confirmed_params = _coerce_mapping(cached_state.get("confirmed_params"))
        risk_tag = str(arguments.get("risk_tag", "")).strip() or str(cached_state.get("risk_tag") or "") or "串围标"
        model_types = _coerce_model_type_filter(arguments.get("model_types"))
        model_name = str(arguments.get("model_name", "")).strip() or str(cached_state.get("model_name") or "").strip()
        confirmed_params = {
            **cached_confirmed_params,
            **_coerce_mapping(arguments.get("confirmed_params")),
        }
        validation_results = {
            **_coerce_mapping(cached_state.get("validation_results")),
            **_coerce_mapping(arguments.get("validation_results")),
        }
        incoming_confirmed_params = _coerce_mapping(arguments.get("confirmed_params"))
        parameter_update_detected = bool(cached_confirmed_params) and any(
            cached_confirmed_params.get(key) != value
            for key, value in incoming_confirmed_params.items()
        )
        execution_confirmed = bool(arguments.get("execution_confirmed")) and not parameter_update_detected

        if not risk_tag:
            return {
                "ok": False,
                "stage": "identify_risk_tag",
                "next_action": "clarify_risk_tag",
                "strict_user_message": "请先明确风险标签，例如“串围标”。",
                "summary": "当前无法识别风险标签。",
            }

        catalog_result = QueryModelCatalogByRiskTool(
            connection_factory=self.connection_factory
        ).execute({"risk_tag": risk_tag, "model_types": model_types}, context=context)
        catalog_items = list(catalog_result.get("items") or [])
        workflow_catalog_items = [_map_workflow_catalog_item(item) for item in catalog_items]
        selected_model = _select_catalog_model(
            catalog_items=catalog_items,
            explicit_model_name=model_name,
            user_query=user_query,
        )
        if selected_model is None:
            suggested_models = _suggest_catalog_models(
                catalog_items=catalog_items,
                explicit_model_name=model_name,
                user_query=user_query,
            )
            return {
                "ok": True,
                "risk_tag": risk_tag,
                "stage": "model_selection",
                "next_action": "select_model",
                "candidate_models": workflow_catalog_items,
                "suggested_models": suggested_models,
                "callable_tools": [
                    {
                        "tool_name": "advance_model_workflow",
                        "purpose": "在用户明确选择模型后继续推进流程",
                        "required_arguments": ["user_query", "risk_tag", "model_name"],
                    }
                ],
                "strict_user_message": _build_model_selection_message(
                    workflow_catalog_items,
                    risk_tag,
                    suggested_models=suggested_models,
                ),
                "summary": "已进入模型选择阶段。",
            }

        selected_model_name = str(selected_model.get("model_name") or "")
        parameter_result = QueryModelParametersTool(
            connection_factory=self.connection_factory
        ).execute({"model_name": selected_model_name}, context=context)
        parameter_items = list(parameter_result.get("items") or [])
        extracted_params = {
            str(item.get("param_name") or ""): confirmed_params.get(str(item.get("param_name") or ""))
            for item in parameter_items
            if str(item.get("param_name") or "").strip()
        }
        validation_results = _merge_validation_results_from_cache(
            context=context,
            parameter_items=parameter_items,
            extracted_params=extracted_params,
            validation_results=validation_results,
        )
        pending_validations = _collect_pending_validations(
            parameter_items=parameter_items,
            extracted_params=extracted_params,
            validation_results=validation_results,
        )
        _save_workflow_state(
            context,
            risk_tag=risk_tag,
            model_name=selected_model_name,
            confirmed_params=confirmed_params,
            validation_results=validation_results,
        )
        if pending_validations:
            return {
                "ok": True,
                "risk_tag": risk_tag,
                "stage": "parameter_validation",
                "next_action": "validate_params",
                "selected_model": selected_model,
                "parameter_items": parameter_items,
                "extracted_params": extracted_params,
                "pending_validations": pending_validations,
                "callable_tools": [
                    {
                        "tool_name": item["validator_tool"],
                        "purpose": f"校验参数 {item['param_name']}",
                        "required_arguments": ["company_name"]
                        if item["validator_tool"] == "query_group_unit_master"
                        else [],
                        "suggested_arguments": (
                            {"company_name": item["candidate_value"]}
                            if item["validator_tool"] == "query_group_unit_master"
                            else {}
                        ),
                    }
                    for item in pending_validations
                ],
                "strict_user_message": _build_validation_message(pending_validations),
                "summary": f"模型“{selected_model_name}”还有 {len(pending_validations)} 个参数需要校验。",
            }

        missing_required_params = _collect_workflow_missing_params(
            parameter_items=parameter_items,
            extracted_params=extracted_params,
            validation_results=validation_results,
        )
        subject_role = str(confirmed_params.get("subject_role") or "").strip()
        zbdw_validation = validation_results.get("zbdw")
        zbdw_validated = isinstance(zbdw_validation, dict) and bool(zbdw_validation.get("ok"))
        if zbdw_validated and not subject_role:
            return {
                "ok": True,
                "risk_tag": risk_tag,
                "stage": "subject_role_confirmation",
                "next_action": "confirm_subject_role",
                "selected_model": selected_model,
                "parameter_items": parameter_items,
                "extracted_params": extracted_params,
                "missing_required_params": missing_required_params,
                "callable_tools": [
                    {
                        "tool_name": "advance_model_workflow",
                        "purpose": "在用户确认主体口径后继续推进流程",
                        "required_arguments": [
                            "user_query",
                            "risk_tag",
                            "model_name",
                            "confirmed_params",
                            "validation_results",
                        ],
                    }
                ],
                "strict_user_message": _build_subject_role_confirmation_message(
                    str(extracted_params.get("zbdw") or ""),
                    missing_required_params=missing_required_params,
                ),
                "summary": f"主体“{str(extracted_params.get('zbdw') or '')}”已校验通过，等待确认口径。",
            }
        if missing_required_params:
            return {
                "ok": True,
                "risk_tag": risk_tag,
                "stage": "parameter_clarification",
                "next_action": "clarify_params",
                "selected_model": selected_model,
                "parameter_items": parameter_items,
                "extracted_params": extracted_params,
                "missing_required_params": missing_required_params,
                "callable_tools": [
                    {
                        "tool_name": "advance_model_workflow",
                        "purpose": "在模型补齐 confirmed_params 后继续推进流程",
                        "required_arguments": [
                            "user_query",
                            "risk_tag",
                            "model_name",
                            "confirmed_params",
                        ],
                    }
                ],
                "strict_user_message": _build_model_param_clarification_message(missing_required_params),
                "summary": f"模型“{selected_model_name}”仍缺少 {len(missing_required_params)} 个必填参数。",
            }

        execution_brief = _build_execution_brief(
            selected_model=selected_model,
            extracted_params=extracted_params,
            subject_role=subject_role,
        )
        if not execution_confirmed:
            return {
                "ok": True,
                "risk_tag": risk_tag,
                "stage": "execution_confirmation",
                "next_action": "confirm_execution",
                "selected_model": selected_model,
                "parameter_items": parameter_items,
                "extracted_params": extracted_params,
                "execution_brief": execution_brief,
                "execution_params": execution_brief["execution_params"],
                "callable_tools": [
                    {
                        "tool_name": "advance_model_workflow",
                        "purpose": "在用户明确确认执行后切换到 ready_to_execute",
                        "required_arguments": [
                            "user_query",
                            "risk_tag",
                            "model_name",
                            "confirmed_params",
                            "validation_results",
                            "execution_confirmed",
                        ],
                    }
                ],
                "strict_user_message": _build_execution_confirmation_message(execution_brief),
                "summary": f"模型“{selected_model_name}”参数已补齐，等待用户确认执行。",
            }

        return {
            "ok": True,
            "risk_tag": risk_tag,
            "stage": "ready_to_execute",
            "next_action": "execute_model",
            "selected_model": selected_model,
            "parameter_items": parameter_items,
            "extracted_params": extracted_params,
            "execution_brief": execution_brief,
            "execution_params": execution_brief["execution_params"],
            "callable_tools": [
                {
                    "tool_name": "execute_registered_model",
                    "purpose": "执行已确认模型并返回可分页结果集预览",
                    "required_arguments": ["model_name", "params"],
                    "suggested_arguments": {
                        "model_name": selected_model_name,
                        "params": execution_brief["execution_params"],
                    },
                },
                {
                    "tool_name": "query_model_result_table",
                    "purpose": "使用 execute_registered_model 返回的 query_handle 分页或筛选读取模型结果集",
                    "required_arguments": ["query_handle"],
                }
            ],
            "summary": f"模型“{selected_model_name}”已完成确认，可以执行。",
        }


def _default_executor_registry() -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "bid_collusion_scene_screening": _execute_bid_collusion_scene_model,
        "bid_collusion_detail_drilldown": _execute_sql_preview_model,
        "sql_preview_executor": _execute_sql_preview_model,
        "national_pipeline_bid_collusion_scene_v1": _execute_sql_preview_model,
    }


class ExecuteRegisteredModelTool(BaseTool):
    name = "execute_registered_model"
    description = "按模型目录中的 executor_code 路由执行器，创建可分页查询的模型结果集预览。只能在 advance_model_workflow 返回 stage=ready_to_execute 且用户已确认执行后调用；用户只回复模型名称时不能直接调用。SQL 表驱动模型不落库，只返回 schema、preview_rows 和 query_handle。preview_rows 仅用于界面预览，不能据此生成最终分析；拿到 query_handle 后必须继续调用 query_model_result_table 读取当前页数据再分析。"
    input_schema = {
        "type": "object",
        "properties": {
            "model_name": {"type": "string", "description": "模型名称"},
            "params": {"type": "object", "description": "模型执行参数"},
        },
        "required": ["model_name", "params"],
    }

    def __init__(
        self,
        connection_factory: Callable[[], Any] | None = None,
        executor_registry: dict[str, Callable[..., dict[str, Any]]] | None = None,
    ):
        self.connection_factory = connection_factory or _default_connection_factory
        self.executor_registry = executor_registry

    def execute(self, arguments: dict, context) -> dict:
        model_name = str(arguments.get("model_name", "")).strip()
        params = _coerce_mapping(arguments.get("params"))
        model_row = _load_model_catalog_row(self.connection_factory, model_name)
        if model_row is None:
            return {
                "ok": False,
                "model_name": model_name,
                "summary": f"未在模型目录中找到模型“{model_name}”。",
            }
        executor_code = str(model_row.get("executor_code") or "").strip()
        if not executor_code and _load_model_sql_config(
            self.connection_factory,
            model_id=str(model_row.get("model_id") or model_row.get("id") or ""),
            sql_code="main",
        ):
            executor_code = "sql_preview_executor"
        executor_registry = self.executor_registry or _default_executor_registry()
        executor = executor_registry.get(executor_code)
        if executor is None:
            return {
                "ok": False,
                "model_name": model_name,
                "executor_code": executor_code,
                "summary": f"模型“{model_name}”未注册执行器 {executor_code}。",
            }
        task_id = getattr(context, "task_id", None) if context is not None else None
        session_id = getattr(context, "session_id", None) if context is not None else None
        result = executor(
            connection_factory=self.connection_factory,
            model_row=model_row,
            arguments=params,
            execution_id=str(uuid4()),
            session_id=session_id,
            task_id=task_id,
        )
        payload = result if isinstance(result, dict) else {}
        return {
            "ok": True,
            "model_name": model_name,
            "executor_code": executor_code,
            **payload,
        }


class QueryModelResultTableTool(BaseTool):
    name = "query_model_result_table"
    description = "按模型 SQL 配置分页读取模型结果集。结果集可能有百万级数据，必须按用户目标传 filters/order_by/page/page_size 分页读取，禁止一次性拉全量或用超大 page_size 塞入上下文。优先使用 execute_registered_model 返回的 query_handle。"
    input_schema = {
        "type": "object",
        "properties": {
            "model_name": {"type": "string", "description": "模型名称"},
            "model_id": {"type": "string", "description": "模型ID"},
            "sql_code": {"type": "string", "description": "SQL配置编码，默认 main"},
            "query_handle": {"type": "object", "description": "execute_registered_model 返回的查询句柄"},
            "filters": {"type": "object", "description": "按 schema 白名单过滤的字段条件"},
            "order_by": {"type": "object", "description": "排序，例如 {column:'共同参与次数', direction:'DESC'}"},
            "page": {"type": "integer", "description": "页码，从 1 开始"},
            "page_size": {"type": "integer", "description": "每页数量，默认 20。结果集可能很大，应按分析需要小批量读取，不要请求超大页。"},
            "limit": {"type": "integer", "description": "兼容旧参数，等同 page_size"},
        },
        "required": [],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        query_handle = _coerce_mapping(arguments.get("query_handle"))
        model_name = str(arguments.get("model_name") or query_handle.get("model_name") or "").strip()
        model_id = str(arguments.get("model_id") or query_handle.get("model_id") or "").strip()
        sql_code = str(arguments.get("sql_code") or query_handle.get("sql_code") or "main").strip() or "main"
        page = max(1, int(arguments.get("page") or 1))
        page_size = _coerce_limit(arguments.get("page_size") or arguments.get("limit"), default=20)
        filters = _merge_dataset_filters(query_handle, _coerce_mapping(arguments.get("filters")))
        order_by = _coerce_mapping(arguments.get("order_by"))
        if query_handle and model_id:
            model_row = {
                "model_id": model_id,
                "model_name": model_name or model_id,
            }
        else:
            model_row = _load_model_catalog_row(self.connection_factory, model_name=model_name, model_id=model_id)
        if model_row is None:
            if not model_id:
                return {
                    "ok": False,
                    "model_name": model_name,
                    "model_id": model_id,
                    "rows": [],
                    "row_count": 0,
                    "summary": f"未在模型目录中找到模型“{model_name or model_id}”。",
                }
            model_row = {
                "model_id": model_id,
                "model_name": model_name or model_id,
            }
        sql_config = _load_model_sql_config(
            self.connection_factory,
            model_id=str(model_row.get("model_id") or model_row.get("id") or ""),
            sql_code=sql_code,
        )
        if sql_config is None:
            return {
                "ok": False,
                "model_name": model_row.get("model_name"),
                "model_id": model_row.get("model_id"),
                "rows": [],
                "row_count": 0,
                "summary": f"未找到模型“{model_row.get('model_name')}”的 SQL 配置。",
            }
        schema = _coerce_sql_schema(sql_config.get("sql_schema"))
        rows = _query_configured_sql_page(
            connection_factory=self.connection_factory,
            sql_text=str(sql_config.get("sql_text") or ""),
            schema=schema,
            model_id=str(model_row.get("model_id") or ""),
            filters=filters,
            order_by=order_by,
            page=page,
            page_size=page_size,
        )
        has_more = len(rows) > page_size
        visible_rows = rows[:page_size]
        response_schema = _display_schema_for_model(
            model_id=str(model_row.get("model_id") or ""),
            schema=schema,
            filters=filters,
        )
        return {
            "ok": True,
            "type": MODEL_RESULT_DATASET_TYPE,
            "artifact_type": MODEL_RESULT_DATASET_TYPE,
            "tool_type": "model_result_query",
            "model_name": model_row.get("model_name"),
            "model_id": model_row.get("model_id"),
            "sql_code": sql_config.get("sql_code"),
            "schema": response_schema,
            "query_handle": _build_query_handle(model_row, sql_config, {"filters": filters}),
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
            "row_count": len(visible_rows),
            "rows": visible_rows,
            "summary": (
                f"已读取模型“{model_row.get('model_name')}”第 {page} 页 {len(visible_rows)} 条记录。"
                if visible_rows
                else f"模型“{model_row.get('model_name')}”当前页没有记录。"
            ),
        }


def _report_template_for_type(report_type: str) -> dict[str, Any]:
    shared_rules = [
        "只能基于 report_context 中的数据成文。",
        "模型命中只能表述为疑似风险线索，不作事实定性、法律定性或责任认定。",
        "没有详情下钻结果时，应先按 recommended_detail_requests 调用详情工具并传回详情 query_handle 做全量详情聚合；详情确实为空时，证据章节披露未查询到明细。",
        "第六章证据明细必须遵循 detail_evidence.items[].display_note：display_mode=full 时列出 representative_rows 全部明细；display_mode=excerpt 时写明“摘录前 N 条 / 共 M 条”。",
        "不得输出下载链接或文件 URL。",
    ]
    if report_type == "single_dimension":
        return {
            "sections": [
                "一、报告背景与分析范围",
                "二、单一模型执行情况",
                "三、命中结果概览",
                "四、重点投标人组合",
                "五、典型证据明细",
                "六、维度风险特征总结",
                "七、核查建议",
                "八、数据限制说明",
            ],
            "strict_rules": [
                *shared_rules,
                "单一模型报告只能围绕 context.dimension 指定的风险维度成文，不得扩展成多维场景综合报告。",
            ],
        }
    return {
        "sections": [
            "一、报告背景与分析范围",
            "二、模型执行情况",
            "三、总体风险概览",
            "四、风险维度分布",
            "五、重点投标人组合分析",
            "六、典型证据明细",
            "七、风险提示与后续核查建议",
            "八、数据限制说明",
        ],
        "strict_rules": shared_rules,
    }


class PrepareBidCollusionReportContextTool(BaseTool):
    name = "prepare_bid_collusion_report_context"
    description = "根据场景模型 query_handle 和详情 query_handle，在后端聚合串围标 Word/PDF 报告上下文。只允许传轻量句柄和报告设置；禁止把 rows、preview_rows、Markdown 正文、完整工具结果或 report.text 放进 arguments。"
    input_schema = {
        "type": "object",
        "properties": {
            "query_handle": {
                "type": "object",
                "description": "场景模型 query_handle。后端会按该句柄中的筛选条件聚合完整结果集，只返回紧凑报告上下文。",
            },
            "detail_query_handles": {
                "type": "array",
                "items": {"type": "object"},
                "description": "可选，query_bid_collusion_detail 返回的详情 query_handle 列表。后端会按句柄聚合全量详情。",
            },
            "report": {
                "type": "object",
                "description": "报告设置，只允许 title、format 或 formats；禁止包含 text、content、markdown、body、data。",
                "properties": {
                    "title": {"type": "string", "description": "报告标题。"},
                    "format": {"type": "string", "description": "导出格式，例如 docx 或 pdf。"},
                    "formats": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选导出格式列表，例如 ['docx']。",
                    },
                },
                "additionalProperties": False,
            },
            "scope": {
                "type": "object",
                "description": "可选补充口径，例如 subject、subject_role、year、model_name。",
            },
            "top_n": {"type": "integer", "description": "重点投标人组合数量，默认 10，最大 50。"},
        },
        "required": ["query_handle"],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        invalid_large_args = _invalid_report_context_argument_keys(arguments)
        if invalid_large_args:
            return {
                "ok": False,
                "type": "bid_collusion_report_context",
                "summary": "报告上下文工具只接受轻量句柄参数，不能接收页面数据、完整工具结果或报告正文。",
                "invalid_arguments": invalid_large_args,
                "required_arguments": {
                    "query_handle": "使用 execute_registered_model 或 query_model_result_table 返回的 query_handle",
                    "detail_query_handles": "可选，query_bid_collusion_detail 返回的 query_handle 列表",
                    "report": {"title": "报告标题", "format": "docx"},
                },
                "required_next_tool": "prepare_bid_collusion_report_context",
            }
        model_result: dict[str, Any] = {}
        explicit_query_handle = _coerce_mapping(arguments.get("query_handle"))
        detail_query_handles = [
            {"query_handle": item}
            for item in arguments.get("detail_query_handles", [])
            if isinstance(item, dict)
        ] if isinstance(arguments.get("detail_query_handles"), list) else []
        detail_results = [*detail_query_handles]
        report_options = _coerce_mapping(arguments.get("report"))
        scope_options = _coerce_mapping(arguments.get("scope"))
        top_n = max(1, min(int(arguments.get("top_n") or 10), 50))
        query_handle = explicit_query_handle
        aggregate_context = _build_report_aggregate_from_query_handle(
            connection_factory=self.connection_factory,
            query_handle=query_handle,
            top_n=top_n,
        ) if query_handle else None
        rows = list(aggregate_context.get("top_rows", [])) if aggregate_context else []
        warnings: list[str] = []
        if not rows:
            return {
                "ok": False,
                "type": "bid_collusion_report_context",
                "summary": "缺少可用 query_handle，无法生成稳定报告上下文。请传入场景模型 query_handle。",
                "warnings": warnings,
                "required_next_tool": "prepare_bid_collusion_report_context",
            }

        filters = _merge_dataset_filters(query_handle, {})
        scope = _build_report_scope(rows, filters, scope_options, model_result)
        title = str(report_options.get("title") or _default_bid_collusion_report_title(scope)).strip()
        raw_report_format = report_options.get("format")
        if not raw_report_format and isinstance(report_options.get("formats"), list):
            raw_report_format = report_options["formats"][0] if report_options["formats"] else None
        report_format = str(raw_report_format or "docx").strip() or "docx"
        row_count = _coerce_report_count(
            aggregate_context.get("row_count") if aggregate_context else model_result.get("row_count"),
            fallback=len(rows),
        )
        detail_context = _summarize_report_details(
            detail_results,
            connection_factory=self.connection_factory,
        )
        if not detail_context["items"]:
            warnings.append("当前未提供详情下钻结果；模型应优先针对重点投标人组合调用 query_bid_collusion_detail 获取证据明细。")
        risk_distribution = (
            aggregate_context.get("risk_level_distribution")
            if aggregate_context
            else _distribution(rows, "风险等级")
        )
        dimension_totals = (
            aggregate_context.get("dimension_totals")
            if aggregate_context
            else _dimension_totals(rows)
        )
        report_type = str(aggregate_context.get("report_type") or "scene") if aggregate_context else "scene"
        dimension_context = aggregate_context.get("dimension") if aggregate_context else None
        dimension_spec = (
            get_dimension_by_single_model_id(str(dimension_context.get("single_model_id") or ""))
            if isinstance(dimension_context, dict)
            else None
        )
        top_pairs = (
            _top_bidder_pairs_for_dimension(rows, dimension=dimension_spec, limit=top_n)
            if dimension_spec is not None
            else _top_bidder_pairs(rows, limit=top_n)
        )
        recommended_detail_requests = _recommended_report_detail_requests(top_pairs)
        if not detail_context["items"] and recommended_detail_requests:
            return {
                "ok": False,
                "type": "bid_collusion_report_context",
                "stage": "detail_required",
                "report_type": report_type,
                "summary": "已完成场景模型后端聚合，但生成报告前必须先按推荐参数下钻详情，不能直接基于汇总结果编写证据章节。",
                "report": {
                    "title": title,
                    "format": report_format,
                },
                "context": {
                    "report_type": report_type,
                    "dimension": dimension_context,
                    "scope": scope,
                    "model": {
                        "model_name": query_handle.get("model_name") or scope.get("model_name"),
                        "model_id": query_handle.get("model_id"),
                        "sql_code": query_handle.get("sql_code"),
                    },
                    "summary": {
                        "row_count": row_count,
                        "evidence_sample_count": len(rows),
                        "risk_level_distribution": risk_distribution,
                        "dimension_totals": dimension_totals,
                    },
                    "top_bidder_pairs": top_pairs,
                    "recommended_detail_requests": recommended_detail_requests,
                    "detail_evidence": detail_context,
                },
                "required_next_tool": "query_bid_collusion_detail",
                "warnings": [
                    "报告生成前必须先调用 query_bid_collusion_detail 获取详情 query_handle，再把 detail_query_handles 传回 prepare_bid_collusion_report_context。"
                ],
            }

        return {
            "ok": True,
            "type": "bid_collusion_report_context",
            "report_type": report_type,
            "report": {
                "title": title,
                "format": report_format,
                "draft_start": f'<report_draft title="{title}" format="{report_format}">',
                "draft_end": "</report_draft>",
            },
            "template": _report_template_for_type(report_type),
            "context": {
                "report_type": report_type,
                "dimension": dimension_context,
                "scope": scope,
                "model": {
                    "model_name": model_result.get("model_name") or query_handle.get("model_name") or scope.get("model_name"),
                    "model_id": model_result.get("model_id") or query_handle.get("model_id"),
                    "sql_code": model_result.get("sql_code") or query_handle.get("sql_code"),
                },
                "summary": {
                    "row_count": row_count,
                    "evidence_sample_count": len(rows),
                    "risk_level_distribution": risk_distribution,
                    "dimension_totals": dimension_totals,
                },
                "top_bidder_pairs": top_pairs,
                "recommended_detail_requests": recommended_detail_requests,
                "detail_evidence": detail_context,
                "limitations": _report_limitations(warnings, model_result, detail_context),
            },
            "warnings": warnings,
            "summary": f"已生成“{title}”的报告上下文：后端聚合结果总数 {row_count} 条，并返回 {len(rows)} 条重点组合用于成文。",
        }


class AdvanceReportExportWorkflowTool(BaseTool):
    name = "advance_report_export_workflow"
    description = "串围标报告导出工作流入口。按阶段返回下一步工具、所需参数和最终报告上下文；报告生成必须先调用它，不要直接写报告或把正文放进工具参数。"
    input_schema = {
        "type": "object",
        "properties": {
            "query_handle": {
                "type": "object",
                "description": "场景模型 query_handle，通常来自 execute_registered_model 或 query_model_result_table。",
            },
            "detail_query_handles": {
                "type": "array",
                "items": {"type": "object"},
                "description": "已完成详情下钻后，由 query_bid_collusion_detail 返回的详情 query_handle 列表。",
            },
            "report": {
                "type": "object",
                "description": "报告设置，只允许 title、format 或 formats；禁止包含正文。",
                "properties": {
                    "title": {"type": "string"},
                    "format": {"type": "string"},
                    "formats": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
            "scope": {
                "type": "object",
                "description": "报告口径补充，例如 subject、subject_role、year、model_name。",
            },
            "top_n": {"type": "integer", "description": "重点组合数量，默认 10。"},
        },
        "required": ["query_handle"],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        invalid_args = _invalid_report_context_argument_keys(arguments)
        if invalid_args:
            return {
                "ok": False,
                "type": "report_export_workflow",
                "stage": "invalid_arguments",
                "summary": "报告工作流只接收轻量句柄和报告设置，不能接收 rows、preview_rows、完整工具结果或 Markdown 正文。",
                "invalid_arguments": invalid_args,
                "next_action": "retry_workflow",
                "next_tool": "advance_report_export_workflow",
                "required_arguments": {
                    "query_handle": "场景模型 query_handle",
                    "detail_query_handles": "可选，详情 query_handle 列表",
                    "report": {"title": "报告标题", "format": "docx"},
                },
            }

        query_handle = _coerce_mapping(arguments.get("query_handle"))
        if not query_handle:
            return {
                "ok": False,
                "type": "report_export_workflow",
                "stage": "need_model_result",
                "summary": "缺少场景模型 query_handle，不能生成报告。请先执行模型或从已读结果中取 query_handle。",
                "next_action": "execute_or_read_model_result",
                "next_tool": "execute_registered_model",
                "required_arguments": {
                    "model_name": "串围标场景筛查",
                    "params": "沿用当前已确认的主体、年度等参数",
                },
            }

        detail_query_handles = [
            item for item in arguments.get("detail_query_handles", [])
            if isinstance(item, dict)
        ] if isinstance(arguments.get("detail_query_handles"), list) else []
        common_args = {
            "query_handle": query_handle,
            "detail_query_handles": detail_query_handles,
            "report": _coerce_mapping(arguments.get("report")),
            "scope": _coerce_mapping(arguments.get("scope")),
            "top_n": arguments.get("top_n") or 10,
        }
        report_context_tool = PrepareBidCollusionReportContextTool(
            connection_factory=self.connection_factory,
        )
        prepared = report_context_tool.execute(common_args, context)

        if prepared.get("stage") == "detail_required":
            recommended = prepared.get("context", {}).get("recommended_detail_requests", [])
            next_detail = recommended[0] if recommended else {}
            return {
                "ok": True,
                "type": "report_export_workflow",
                "stage": "detail_required",
                "report_type": prepared.get("report_type") or prepared.get("context", {}).get("report_type") or "scene",
                "summary": prepared.get("summary"),
                "next_action": "call_detail_tool",
                "next_tool": "query_bid_collusion_detail",
                "tool_arguments": next_detail.get("arguments", {}),
                "all_recommended_detail_requests": recommended,
                "after_tool": {
                    "next_tool": "advance_report_export_workflow",
                    "arguments": {
                        "query_handle": query_handle,
                        "detail_query_handles": ["把 query_bid_collusion_detail 返回的 query_handle 放到这里"],
                        "report": common_args["report"],
                        "scope": common_args["scope"],
                        "top_n": common_args["top_n"],
                    },
                },
                "workflow_state": {
                    "query_handle": query_handle,
                    "report": common_args["report"],
                    "scope": common_args["scope"],
                    "top_n": common_args["top_n"],
                },
                "context_preview": prepared.get("context"),
            }

        if prepared.get("ok") is not True:
            return {
                "ok": False,
                "type": "report_export_workflow",
                "stage": prepared.get("stage") or "blocked",
                "summary": prepared.get("summary"),
                "next_action": "fix_arguments",
                "next_tool": prepared.get("required_next_tool") or "advance_report_export_workflow",
                "required_arguments": prepared.get("required_arguments"),
                "warnings": prepared.get("warnings", []),
            }

        return {
            "ok": True,
            "type": "report_export_workflow",
            "stage": "ready_to_write_report",
            "report_type": prepared.get("report_type") or prepared.get("context", {}).get("report_type") or "scene",
            "summary": "报告上下文已准备完成。下一步不要再调用导出工具，直接在 assistant 回复中流式输出 report_draft Markdown。",
            "next_action": "write_report_draft",
            "next_tool": None,
            "report": prepared.get("report"),
            "template": prepared.get("template"),
            "context": prepared.get("context"),
            "strict_output": {
                "format": "<report_draft title=\"...\" format=\"docx\">\\n# 报告标题\\n...\\n</report_draft>",
                "rules": [
                    "report_draft 必须是本轮回复的第一个可见内容。",
                    "不得输出下载链接或文件 URL。",
                    "只能基于本工具返回的 context 写报告。",
                    "不要再调用 export_report_document；前端会在闭合标签后自动导出。",
                ],
            },
        }


class QueryBizIndicatorTool(BaseTool):
    name = "query_biz_indicator"
    description = (
        "查询 biz_indicators 指标库中已经固化的单项统计指标。"
        "仅当用户明确询问某个指标名称、指标值、统计口径、指标清单，"
        "或问题可以直接映射为 biz_indicators.indicator_name 时使用；"
        "不要用于 Ontology 语义指标或治理资产；用户询问风险指标、规则阈值、动作通知、模型结果映射、审计发现或多实体关系时，应使用 resolve_enterprise_ontology。"
        "支持精确匹配、语义关键词候选，以及显式返回指标清单。"
        "本工具不执行明细数据查询、不生成 SQL、不分析模型结果、不做风险/专项场景编排。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "indicator_name": {
                "type": "string",
                "description": "待查询的指标名称，例如 2025年的合同金额（亿元）。只填写真实指标名称，不要填写查询语句。",
            },
            "list_all": {
                "type": "boolean",
                "description": "是否返回指标清单；用户想查看全部指标或指标列表时传 true",
            },
            "limit": {"type": "integer", "description": "返回数量上限，默认 100"},
        },
        "required": [],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        query_text = _normalize_indicator_phrase(str(arguments.get("indicator_name", "")).strip())
        list_all = bool(arguments.get("list_all"))
        limit = _coerce_limit(arguments.get("limit"), default=100)
        if list_all:
            sql = """
SELECT
    indicator_id,
    indicator_name,
    indicator_value,
    stat_date,
    create_time
FROM public.biz_indicators
ORDER BY stat_date DESC NULLS LAST, indicator_name
LIMIT %s
""".strip()
            rows = self._run_query(sql, [limit])
            items = [_map_biz_indicator_row(row) for row in rows]
            return {
                "ok": True,
                "query_text": query_text,
                "match_type": "all",
                "need_confirmation": False,
                "matched_item": None,
                "candidates": [],
                "items": items,
                "total_returned": len(items),
                "summary": f"已返回 {len(items)} 条业务指标记录。",
            }

        if not query_text:
            return {
                "ok": False,
                "query_text": "",
                "match_type": "invalid_arguments",
                "need_confirmation": False,
                "matched_item": None,
                "candidates": [],
                "items": [],
                "total_returned": 0,
                "summary": "请提供 indicator_name，或使用 list_all=true 查询指标清单。",
            }

        exact_sql = """
SELECT
    indicator_id,
    indicator_name,
    indicator_value,
    stat_date,
    create_time
FROM public.biz_indicators
WHERE indicator_name = %s
ORDER BY stat_date DESC NULLS LAST, create_time DESC NULLS LAST, indicator_id
LIMIT 10
""".strip()
        exact_rows = self._run_query(exact_sql, [query_text])
        if exact_rows:
            matched = _map_biz_indicator_row(exact_rows[0])
            return {
                "ok": True,
                "query_text": query_text,
                "match_type": "exact",
                "need_confirmation": False,
                "matched_item": matched,
                "candidates": [],
                "summary": f"已精确匹配到 1 条业务指标：{matched['name']}，值为 {matched['value']}。",
            }

        terms = _indicator_search_terms(query_text)
        where_clause = " OR ".join(["indicator_name ILIKE %s" for _ in terms])
        candidate_sql = f"""
SELECT
    indicator_id,
    indicator_name,
    indicator_value,
    stat_date,
    create_time
FROM public.biz_indicators
WHERE {where_clause}
ORDER BY stat_date DESC NULLS LAST, indicator_name
LIMIT 10
""".strip()
        candidate_rows = self._run_query(candidate_sql, [f"%{term}%" for term in terms])
        candidates = sorted(
            [_map_biz_indicator_row(row) for row in candidate_rows],
            key=lambda item: _indicator_candidate_score(query_text, str(item.get("name") or "")),
            reverse=True,
        )
        return {
            "ok": True,
            "query_text": query_text,
            "match_type": "semantic_candidate" if candidates else "none",
            "need_confirmation": bool(candidates),
            "matched_item": None,
            "candidates": candidates,
            "summary": (
                f"未精确匹配到业务指标，按语义关键词返回 {len(candidates)} 个候选项供确认。"
                if candidates
                else f"未在业务指标表中找到与“{query_text}”匹配的记录。"
            ),
        }

    def _run_query(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        conn = self.connection_factory()
        try:
            cur = conn.cursor()
            try:
                _log_sql_execution(f"{self.name}._run_query", sql, params)
                cur.execute(sql, params)
                records = cur.fetchall()
                columns = [item[0] for item in cur.description]
            finally:
                cur.close()
        finally:
            conn.close()
        return _as_rows(columns, records)


BID_COLLUSION_CTE_SQL = """
WITH
check_table1 AS (
SELECT
    t1.name,
    t1.zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    substr(CAST(t1.cqsj AS VARCHAR), 0, 11) AS cqsj_date,
    t1.tbrmc AS tbrmc1,
    t2.tbrmc AS tbrmc2,
    CASE WHEN t1.sfzb = '是' THEN 1 ELSE 0 END AS sfzb1_num,
    CASE WHEN t2.sfzb = '是' THEN 1 ELSE 0 END AS sfzb2_num,
    '人员重复' AS ydlx,
    NULL AS cpuxlh1,
    NULL AS cpuxlh2,
    NULL AS ypxlh1,
    NULL AS ypxlh2,
    NULL AS wkxlh1,
    NULL AS wkxlh2,
    NULL AS yj_ip1,
    NULL AS yj_ip2,
    NULL AS hkyhzh,
    NULL AS hkzhmc1,
    NULL AS hkzhmc2,
    NULL AS hkkhhmc1,
    NULL AS hkkhhmc2,
    NULL AS kbsj1,
    NULL AS kbsj2,
    NULL AS tb_times1,
    NULL AS tb_times2,
    NULL AS zbr1,
    NULL AS zbr2
FROM
    rst_sj_dzzb_tblxrxt t1
JOIN
    rst_sj_dzzb_tblxrxt t2
ON
    t1.name = t2.name
    AND t1.zbnd = t2.zbnd
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.bdmc = t2.bdmc
    AND substr(CAST(t1.cqsj AS VARCHAR), 0, 11) = substr(CAST(t2.cqsj AS VARCHAR), 0, 11)
    AND t1.tbrmc <> t2.tbrmc
    AND t1.tbrmc < t2.tbrmc
GROUP BY
    t1.name, t1.zbnd, t1.zbdw, t1.xmmc, t1.bdmc, cqsj_date, t1.tbrmc, t2.tbrmc, t1.sfzb, t2.sfzb
ORDER BY
    name, zbnd, xmmc, bdmc, cqsj_date, tbrmc1, tbrmc2
),
check_table2 AS (
SELECT
    t1.name,
    TO_CHAR(t1.cqsj, 'YYYY') AS zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    t1.tbsqrdb AS 代表人1,
    t2.tbsqrdb AS 代表人2,
    t1.tbrmc AS 投标人1,
    t2.tbrmc AS 投标人2,
    substr(cast(t1.cqsj as varchar), 0, 11) as cqsj1,
    '电话邮箱相同' AS ydlx,
    NULL AS cpuxlh1,
    NULL AS cpuxlh2,
    NULL AS ypxlh1,
    NULL AS ypxlh2,
    NULL AS wkxlh1,
    NULL AS wkxlh2,
    NULL AS yj_ip1,
    NULL AS yj_ip2,
    NULL AS hkyhzh,
    NULL AS hkzhmc1,
    NULL AS hkzhmc2,
    NULL AS hkkhhmc1,
    NULL AS hkkhhmc2,
    NULL AS kbsj1,
    NULL AS kbsj2,
    NULL AS tb_times1,
    NULL AS tb_times2,
    NULL AS zbr1,
    NULL AS zbr2
FROM
    rst_sj_dzzb_tbrdhyxxt t1
JOIN
    rst_sj_dzzb_tbrdhyxxt t2
ON
    t1.name = t2.name
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.bdmc = t2.bdmc
    AND t1.tbsqrdbdh = t2.tbsqrdbdh
    AND t1.tbsqrdbyx = t2.tbsqrdbyx
    AND t1.tbsqrdb <> t2.tbsqrdb
    AND t1.tbsqrdb < t2.tbsqrdb
GROUP BY
    t1.name, zbnd, t1.zbdw, t1.xmmc, t1.bdmc,
    t1.tbsqrdbdh, t1.tbsqrdbyx, t1.tbsqrdb, t2.tbsqrdb,
    t1.tbrmc, t2.tbrmc, cqsj1
ORDER BY
    name, zbdw, xmmc, bdmc
),
check_table3 AS (
SELECT
    t1.name,
    TO_CHAR(t1.cqsj, 'YYYY') AS zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    TO_CHAR(t1.cqsj, 'YYYY-MM-DD') AS cqsj_date,
    t1.tbrmc AS tbrmc1,
    t2.tbrmc AS tbrmc2,
    0 AS sfzb1_num,
    0 AS sfzb2_num,
    '硬件或IP重复' AS ydlx,
    t1.cpuxlh AS cpuxlh1,
    t2.cpuxlh AS cpuxlh2,
    t1.ypxlh AS ypxlh1,
    t2.ypxlh AS ypxlh2,
    t1.wkxlh AS wkxlh1,
    t2.wkxlh AS wkxlh2,
    t1.yj_ip AS yj_ip1,
    t2.yj_ip AS yj_ip2,
    NULL AS hkyhzh,
    NULL AS hkzhmc1,
    NULL AS hkzhmc2,
    NULL AS hkkhhmc1,
    NULL AS hkkhhmc2,
    NULL AS kbsj1,
    NULL AS kbsj2,
    NULL AS tb_times1,
    NULL AS tb_times2,
    NULL AS zbr1,
    NULL AS zbr2
FROM
    rst_sj_dzzb_yjjipyz t1
JOIN
    rst_sj_dzzb_yjjipyz t2
ON
    t1.name = t2.name
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.bdmc = t2.bdmc
    AND TO_CHAR(t1.cqsj, 'YYYY-MM-DD') = TO_CHAR(t2.cqsj, 'YYYY-MM-DD')
    AND t1.tbrmc <> t2.tbrmc
    AND t1.tbrmc < t2.tbrmc
GROUP BY
    t1.name, zbnd, t1.zbdw, t1.xmmc, t1.bdmc, cqsj_date, t1.tbrmc, t2.tbrmc,
    t1.cpuxlh, t2.cpuxlh, t1.ypxlh, t2.ypxlh, t1.wkxlh, t2.wkxlh, t1.yj_ip, t2.yj_ip
),
check_table4 AS (
SELECT
    t1.name,
    TO_CHAR(t1.cqsj, 'YYYY') AS zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    TO_CHAR(t1.cqsj, 'YYYY-MM-DD') AS cqsj_date,
    t1.tbrmc AS tbrmc1,
    t2.tbrmc AS tbrmc2,
    0 AS sfzb1_num,
    0 AS sfzb2_num,
    '保证金同一账户转出' AS ydlx,
    NULL AS cpuxlh1,
    NULL AS cpuxlh2,
    NULL AS ypxlh1,
    NULL AS ypxlh2,
    NULL AS wkxlh1,
    NULL AS wkxlh2,
    NULL AS yj_ip1,
    NULL AS yj_ip2,
    t1.hkyhzh AS hkyhzh,
    t1.hkzhmc AS hkzhmc1,
    t2.hkzhmc AS hkzhmc2,
    t1.hkkhhmc AS hkkhhmc1,
    t2.hkkhhmc AS hkkhhmc2,
    NULL AS kbsj1,
    NULL AS kbsj2,
    NULL AS tb_times1,
    NULL AS tb_times2,
    NULL AS zbr1,
    NULL AS zbr2
FROM
    rst_sj_dzzb_tbbzjctyzhzc t1
JOIN
    rst_sj_dzzb_tbbzjctyzhzc t2
ON
    t1.name = t2.name
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.bdmc = t2.bdmc
    AND t1.hkyhzh = t2.hkyhzh
    AND TO_CHAR(t1.cqsj, 'YYYY-MM-DD') = TO_CHAR(t2.cqsj, 'YYYY-MM-DD')
    AND t1.tbrmc <> t2.tbrmc
    AND t1.tbrmc < t2.tbrmc
GROUP BY
    t1.name, zbnd, t1.zbdw, t1.xmmc, t1.bdmc, cqsj_date, t1.tbrmc, t2.tbrmc,
    t1.hkyhzh, t1.hkzhmc, t2.hkzhmc, t1.hkkhhmc, t2.hkkhhmc
),
check_table5 AS (
SELECT
    t1.name,
    TO_CHAR(t1.cqsj, 'YYYY') AS zbnd,
    t1.zbdw,
    t1.xmmc,
    '' AS bdmc,
    TO_CHAR(t1.cqsj, 'YYYY-MM-DD') AS cqsj_date,
    t1.tbrmc AS tbrmc1,
    t2.tbrmc AS tbrmc2,
    0 AS sfzb1_num,
    0 AS sfzb2_num,
    '投标人完全一致' AS ydlx,
    NULL AS cpuxlh1,
    NULL AS cpuxlh2,
    NULL AS ypxlh1,
    NULL AS ypxlh2,
    NULL AS wkxlh1,
    NULL AS wkxlh2,
    NULL AS yj_ip1,
    NULL AS yj_ip2,
    NULL AS hkyhzh,
    NULL AS hkzhmc1,
    NULL AS hkzhmc2,
    NULL AS hkkhhmc1,
    NULL AS hkkhhmc2,
    TO_CHAR(t1.kbsj, 'YYYY-MM-DD') AS kbsj1,
    TO_CHAR(t2.kbsj, 'YYYY-MM-DD') AS kbsj2,
    t1.tb_times AS tb_times1,
    t2.tb_times AS tb_times2,
    t1.zbr AS zbr1,
    t2.zbr AS zbr2
FROM
    rst_sj_dzzb_tbrwqyz t1
JOIN
    rst_sj_dzzb_tbrwqyz t2
ON
    t1.name = t2.name
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.tbrmc <> t2.tbrmc
    AND t1.tbrmc < t2.tbrmc
    AND TO_CHAR(t1.cqsj, 'YYYY-MM-DD') = TO_CHAR(t2.cqsj, 'YYYY-MM-DD')
GROUP BY
    t1.name, zbnd, t1.zbdw, t1.xmmc, bdmc, cqsj_date, t1.tbrmc, t2.tbrmc,
    TO_CHAR(t1.kbsj, 'YYYY-MM-DD'), TO_CHAR(t2.kbsj, 'YYYY-MM-DD'),
    t1.tb_times, t2.tb_times, t1.zbr, t2.zbr
)
""".strip()

BID_COLLUSION_ALL_DATA_SQL = f"""
{BID_COLLUSION_CTE_SQL},
all_data AS (
SELECT
    t1.name,
    t1.zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    t1.cqsj_date,
    t1.tbrmc1,
    t1.tbrmc2,
    t1.sfzb1_num,
    t1.sfzb2_num,
    t1.ydlx,
    t1.cpuxlh1,
    t1.cpuxlh2,
    t1.ypxlh1,
    t1.ypxlh2,
    t1.wkxlh1,
    t1.wkxlh2,
    t1.yj_ip1,
    t1.yj_ip2,
    t1.hkyhzh,
    t1.hkzhmc1,
    t1.hkzhmc2,
    t1.hkkhhmc1,
    t1.hkkhhmc2,
    t1.kbsj1,
    t1.kbsj2,
    t1.tb_times1,
    t1.tb_times2,
    t1.zbr1,
    t1.zbr2
FROM check_table1 t1
UNION ALL
SELECT
    t2.name,
    t2.zbnd,
    t2.zbdw,
    t2.xmmc,
    t2.bdmc,
    t2.cqsj1 AS cqsj_date,
    t2.投标人1 AS tbrmc1,
    t2.投标人2 AS tbrmc2,
    0 AS sfzb1_num,
    0 AS sfzb2_num,
    t2.ydlx,
    t2.cpuxlh1,
    t2.cpuxlh2,
    t2.ypxlh1,
    t2.ypxlh2,
    t2.wkxlh1,
    t2.wkxlh2,
    t2.yj_ip1,
    t2.yj_ip2,
    t2.hkyhzh,
    t2.hkzhmc1,
    t2.hkzhmc2,
    t2.hkkhhmc1,
    t2.hkkhhmc2,
    t2.kbsj1,
    t2.kbsj2,
    t2.tb_times1,
    t2.tb_times2,
    t2.zbr1,
    t2.zbr2
FROM check_table2 t2
UNION ALL
SELECT
    t3.name,
    t3.zbnd,
    t3.zbdw,
    t3.xmmc,
    t3.bdmc,
    t3.cqsj_date,
    t3.tbrmc1,
    t3.tbrmc2,
    t3.sfzb1_num,
    t3.sfzb2_num,
    t3.ydlx,
    t3.cpuxlh1,
    t3.cpuxlh2,
    t3.ypxlh1,
    t3.ypxlh2,
    t3.wkxlh1,
    t3.wkxlh2,
    t3.yj_ip1,
    t3.yj_ip2,
    t3.hkyhzh,
    t3.hkzhmc1,
    t3.hkzhmc2,
    t3.hkkhhmc1,
    t3.hkkhhmc2,
    t3.kbsj1,
    t3.kbsj2,
    t3.tb_times1,
    t3.tb_times2,
    t3.zbr1,
    t3.zbr2
FROM check_table3 t3
UNION ALL
SELECT
    t4.name,
    t4.zbnd,
    t4.zbdw,
    t4.xmmc,
    t4.bdmc,
    t4.cqsj_date,
    t4.tbrmc1,
    t4.tbrmc2,
    t4.sfzb1_num,
    t4.sfzb2_num,
    t4.ydlx,
    t4.cpuxlh1,
    t4.cpuxlh2,
    t4.ypxlh1,
    t4.ypxlh2,
    t4.wkxlh1,
    t4.wkxlh2,
    t4.yj_ip1,
    t4.yj_ip2,
    t4.hkyhzh,
    t4.hkzhmc1,
    t4.hkzhmc2,
    t4.hkkhhmc1,
    t4.hkkhhmc2,
    t4.kbsj1,
    t4.kbsj2,
    t4.tb_times1,
    t4.tb_times2,
    t4.zbr1,
    t4.zbr2
FROM check_table4 t4
UNION ALL
SELECT
    t5.name,
    t5.zbnd,
    t5.zbdw,
    t5.xmmc,
    t5.bdmc,
    t5.cqsj_date,
    t5.tbrmc1,
    t5.tbrmc2,
    t5.sfzb1_num,
    t5.sfzb2_num,
    t5.ydlx,
    t5.cpuxlh1,
    t5.cpuxlh2,
    t5.ypxlh1,
    t5.ypxlh2,
    t5.wkxlh1,
    t5.wkxlh2,
    t5.yj_ip1,
    t5.yj_ip2,
    t5.hkyhzh,
    t5.hkzhmc1,
    t5.hkzhmc2,
    t5.hkkhhmc1,
    t5.hkkhhmc2,
    t5.kbsj1,
    t5.kbsj2,
    t5.tb_times1,
    t5.tb_times2,
    t5.zbr1,
    t5.zbr2
FROM check_table5 t5
)
""".strip()

BID_COLLUSION_RISK_CTE_SQL = """
WITH
check_table1 AS (
SELECT
    t1.name,
    t1.zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    substr(CAST(t1.cqsj AS VARCHAR), 0, 11) AS cqsj_date,
    t1.tbrmc AS tbrmc1,
    t2.tbrmc AS tbrmc2,
    CASE WHEN t1.sfzb = '是' THEN 1 ELSE 0 END AS sfzb1_num,
    CASE WHEN t2.sfzb = '是' THEN 1 ELSE 0 END AS sfzb2_num,
    '人员重复' AS ydlx
FROM
    rst_sj_dzzb_tblxrxt t1
JOIN
    rst_sj_dzzb_tblxrxt t2
ON
    t1.name = t2.name
    AND t1.zbnd = t2.zbnd
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.bdmc = t2.bdmc
    AND substr(CAST(t1.cqsj AS VARCHAR), 0, 11) = substr(CAST(t2.cqsj AS VARCHAR), 0, 11)
    AND t1.tbrmc <> t2.tbrmc
    AND t1.tbrmc < t2.tbrmc
GROUP BY
    t1.name, t1.zbnd, t1.zbdw, t1.xmmc, t1.bdmc, cqsj_date, t1.tbrmc, t2.tbrmc, t1.sfzb, t2.sfzb
),
check_table2 AS (
SELECT
    t1.name,
    TO_CHAR(t1.cqsj, 'YYYY') AS zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    t1.tbrmc AS 投标人1,
    t2.tbrmc AS 投标人2,
    substr(cast(t1.cqsj as varchar), 0, 11) as cqsj1,
    '电话邮箱相同' AS ydlx
FROM
    rst_sj_dzzb_tbrdhyxxt t1
JOIN
    rst_sj_dzzb_tbrdhyxxt t2
ON
    t1.name = t2.name
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.bdmc = t2.bdmc
    AND t1.tbsqrdbdh = t2.tbsqrdbdh
    AND t1.tbsqrdbyx = t2.tbsqrdbyx
    AND t1.tbsqrdb <> t2.tbsqrdb
    AND t1.tbsqrdb < t2.tbsqrdb
GROUP BY
    t1.name, zbnd, t1.zbdw, t1.xmmc, t1.bdmc,
    t1.tbsqrdbdh, t1.tbsqrdbyx, t1.tbsqrdb, t2.tbsqrdb,
    t1.tbrmc, t2.tbrmc, cqsj1
),
check_table3 AS (
SELECT
    t1.name,
    TO_CHAR(t1.cqsj, 'YYYY') AS zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    TO_CHAR(t1.cqsj, 'YYYY-MM-DD') AS cqsj_date,
    t1.tbrmc AS tbrmc1,
    t2.tbrmc AS tbrmc2,
    0 AS sfzb1_num,
    0 AS sfzb2_num,
    '硬件或IP重复' AS ydlx
FROM
    rst_sj_dzzb_yjjipyz t1
JOIN
    rst_sj_dzzb_yjjipyz t2
ON
    t1.name = t2.name
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.bdmc = t2.bdmc
    AND TO_CHAR(t1.cqsj, 'YYYY-MM-DD') = TO_CHAR(t2.cqsj, 'YYYY-MM-DD')
    AND t1.tbrmc <> t2.tbrmc
    AND t1.tbrmc < t2.tbrmc
GROUP BY
    t1.name, zbnd, t1.zbdw, t1.xmmc, t1.bdmc, cqsj_date, t1.tbrmc, t2.tbrmc
),
check_table4 AS (
SELECT
    t1.name,
    TO_CHAR(t1.cqsj, 'YYYY') AS zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    TO_CHAR(t1.cqsj, 'YYYY-MM-DD') AS cqsj_date,
    t1.tbrmc AS tbrmc1,
    t2.tbrmc AS tbrmc2,
    0 AS sfzb1_num,
    0 AS sfzb2_num,
    '保证金同一账户转出' AS ydlx
FROM
    rst_sj_dzzb_tbbzjctyzhzc t1
JOIN
    rst_sj_dzzb_tbbzjctyzhzc t2
ON
    t1.name = t2.name
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.bdmc = t2.bdmc
    AND t1.hkyhzh = t2.hkyhzh
    AND TO_CHAR(t1.cqsj, 'YYYY-MM-DD') = TO_CHAR(t2.cqsj, 'YYYY-MM-DD')
    AND t1.tbrmc <> t2.tbrmc
    AND t1.tbrmc < t2.tbrmc
GROUP BY
    t1.name, zbnd, t1.zbdw, t1.xmmc, t1.bdmc, cqsj_date, t1.tbrmc, t2.tbrmc
),
check_table5 AS (
SELECT
    t1.name,
    TO_CHAR(t1.cqsj, 'YYYY') AS zbnd,
    t1.zbdw,
    t1.xmmc,
    '' AS bdmc,
    TO_CHAR(t1.cqsj, 'YYYY-MM-DD') AS cqsj_date,
    t1.tbrmc AS tbrmc1,
    t2.tbrmc AS tbrmc2,
    0 AS sfzb1_num,
    0 AS sfzb2_num,
    '投标人完全一致' AS ydlx
FROM
    rst_sj_dzzb_tbrwqyz t1
JOIN
    rst_sj_dzzb_tbrwqyz t2
ON
    t1.name = t2.name
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.tbrmc <> t2.tbrmc
    AND t1.tbrmc < t2.tbrmc
    AND TO_CHAR(t1.cqsj, 'YYYY-MM-DD') = TO_CHAR(t2.cqsj, 'YYYY-MM-DD')
GROUP BY
    t1.name, zbnd, t1.zbdw, t1.xmmc, bdmc, cqsj_date, t1.tbrmc, t2.tbrmc
),
all_data AS (
SELECT
    t1.name,
    t1.zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    t1.cqsj_date,
    t1.tbrmc1,
    t1.tbrmc2,
    t1.sfzb1_num,
    t1.sfzb2_num,
    t1.ydlx
FROM check_table1 t1
UNION ALL
SELECT
    t2.name,
    t2.zbnd,
    t2.zbdw,
    t2.xmmc,
    t2.bdmc,
    t2.cqsj1 AS cqsj_date,
    t2.投标人1 AS tbrmc1,
    t2.投标人2 AS tbrmc2,
    0 AS sfzb1_num,
    0 AS sfzb2_num,
    t2.ydlx
FROM check_table2 t2
UNION ALL
SELECT
    t3.name,
    t3.zbnd,
    t3.zbdw,
    t3.xmmc,
    t3.bdmc,
    t3.cqsj_date,
    t3.tbrmc1,
    t3.tbrmc2,
    t3.sfzb1_num,
    t3.sfzb2_num,
    t3.ydlx
FROM check_table3 t3
UNION ALL
SELECT
    t4.name,
    t4.zbnd,
    t4.zbdw,
    t4.xmmc,
    t4.bdmc,
    t4.cqsj_date,
    t4.tbrmc1,
    t4.tbrmc2,
    t4.sfzb1_num,
    t4.sfzb2_num,
    t4.ydlx
FROM check_table4 t4
UNION ALL
SELECT
    t5.name,
    t5.zbnd,
    t5.zbdw,
    t5.xmmc,
    t5.bdmc,
    t5.cqsj_date,
    t5.tbrmc1,
    t5.tbrmc2,
    t5.sfzb1_num,
    t5.sfzb2_num,
    t5.ydlx
FROM check_table5 t5
)
""".strip()

BID_COLLUSION_DETAIL_BASE_SQL = f"""
{BID_COLLUSION_RISK_CTE_SQL}
SELECT
    name AS "招标人",
    zbnd AS "招标年度",
    zbdw AS "招标单位",
    xmmc AS "项目名称",
    bdmc AS "标段名称",
    cqsj_date AS "抽取日期",
    tbrmc1 AS "投标人1",
    tbrmc2 AS "投标人2",
    sfzb1_num AS "投标人1中标标记",
    sfzb2_num AS "投标人2中标标记",
    ydlx AS "疑点类型"
FROM all_data
""".strip()

BID_COLLUSION_DETAIL_SQL_SCHEMA = {
    "columns": [
        {"name": "招标人", "meaning": "招标人"},
        {"name": "招标年度", "meaning": "招标年度"},
        {"name": "招标单位", "meaning": "招标单位"},
        {"name": "项目名称", "meaning": "项目名称"},
        {"name": "标段名称", "meaning": "标段名称"},
        {"name": "抽取日期", "meaning": "数据抽取日期"},
        {"name": "投标人1", "meaning": "风险组合中的第一个投标人"},
        {"name": "投标人2", "meaning": "风险组合中的第二个投标人"},
        {"name": "投标人1中标标记", "meaning": "投标人1在该明细中的中标标记，1 表示中标"},
        {"name": "投标人2中标标记", "meaning": "投标人2在该明细中的中标标记，1 表示中标"},
        {"name": "疑点类型", "meaning": "该明细命中的串围标疑点类型"},
    ],
    "filterable_columns": ["投标人1", "投标人2", "疑点类型", "招标人", "招标年度", "招标单位", "项目名称", "标段名称"],
    "exact_filter_columns": ["投标人1", "投标人2", "疑点类型", "招标年度"],
    "default_order_by": "抽取日期 DESC",
}


def build_bid_collusion_query(filters: dict | None = None) -> tuple[str, list[Any]]:
    filters = {
        key: value for key, value in (filters or {}).items() if value not in (None, "", [])
    }
    params: list[Any] = []
    sql_parts = [
        BID_COLLUSION_RISK_CTE_SQL,
        ",",
        """
result AS (
    SELECT
        t.*,
        t.共同参与次数::NUMERIC - t."A中标次数"::NUMERIC - t."B中标次数"::NUMERIC AS 均未中标次数,
        ROUND((t."A中标次数"::NUMERIC + t."B中标次数"::NUMERIC) / t.共同参与次数::NUMERIC * 100, 2) AS 中标集中度
    FROM (
        SELECT
            MIN(name) AS name,
            COALESCE(MIN(NULLIF(zbnd, '')), '') AS zbnd,
            MIN(zbdw) AS zbdw,
            MIN(xmmc) AS xmmc,
            COALESCE(MIN(NULLIF(bdmc, '')), '') AS bdmc,
            MIN(cqsj_date) AS cqsj_date,
            tbrmc1,
            tbrmc2,
            COUNT(*) AS 共同参与次数,
            SUM(CASE WHEN ydlx = '电话邮箱相同' THEN 1 ELSE 0 END) AS 不同投标人电话邮箱相同,
            SUM(CASE WHEN ydlx = '投标人完全一致' THEN 1 ELSE 0 END) AS 不同标段投标人完全一致,
            SUM(CASE WHEN ydlx = '硬件或IP重复' THEN 1 ELSE 0 END) AS 不同投标人硬件及ip地址一致,
            SUM(CASE WHEN ydlx = '保证金同一账户转出' THEN 1 ELSE 0 END) AS 投标保证金从同一账户转出,
            SUM(CASE WHEN ydlx = '人员重复' THEN 1 ELSE 0 END) AS 人员关联次数,
            SUM(sfzb1_num) AS "A中标次数",
            SUM(sfzb2_num) AS "B中标次数"
        FROM all_data
        GROUP BY tbrmc1, tbrmc2
    ) t
)
SELECT *
FROM (
    SELECT
        name,
        zbnd,
        zbdw,
        xmmc,
        bdmc,
        cqsj_date,
        tbrmc1 AS "投标人1",
        tbrmc2 AS "投标人2",
        tbrmc1 || ' / ' || tbrmc2 AS "有关系的投标人组",
        不同投标人电话邮箱相同,
        不同标段投标人完全一致,
        不同投标人硬件及ip地址一致,
        投标保证金从同一账户转出,
        人员关联次数,
        CASE
            WHEN 共同参与次数 >= 10 THEN '高'
            WHEN 共同参与次数 >= 5 THEN '中'
            ELSE '低'
        END AS "风险等级",
        共同参与次数,
        "A中标次数",
        "B中标次数",
        均未中标次数,
        中标集中度 || '%%' AS "中标集中度"
    FROM result
) AS base
WHERE 1=1
""".strip(),
    ]

    if filters.get("zbnd"):
        sql_parts.append('AND base."zbnd" = %s')
        params.append(filters["zbnd"])
    if filters.get("zbdw"):
        sql_parts.append('AND base."zbdw" = %s')
        params.append(filters["zbdw"])
    if filters.get("xmmc"):
        sql_parts.append('AND base."xmmc" ILIKE %s')
        params.append(f'%{filters["xmmc"]}%')
    if filters.get("bdmc"):
        sql_parts.append('AND base."bdmc" ILIKE %s')
        params.append(f'%{filters["bdmc"]}%')
    if filters.get("date_from"):
        sql_parts.append('AND base."cqsj_date" >= %s')
        params.append(filters["date_from"])
    if filters.get("date_to"):
        sql_parts.append('AND base."cqsj_date" <= %s')
        params.append(filters["date_to"])
    if filters.get("tbrmc"):
        sql_parts.append('AND (base."投标人1" ILIKE %s OR base."投标人2" ILIKE %s)')
        bidder = f'%{filters["tbrmc"]}%'
        params.extend([bidder, bidder])
    if filters.get("risk_dimension") == "电话邮箱相同":
        sql_parts.append('AND base."不同投标人电话邮箱相同" > 0')
    elif filters.get("risk_dimension") == "投标人完全一致":
        sql_parts.append('AND base."不同标段投标人完全一致" > 0')
    elif filters.get("risk_dimension") == "硬件或IP重复":
        sql_parts.append('AND base."不同投标人硬件及ip地址一致" > 0')
    elif filters.get("risk_dimension") == "保证金同一账户转出":
        sql_parts.append('AND base."投标保证金从同一账户转出" > 0')
    elif filters.get("risk_dimension") == "人员重复":
        sql_parts.append(f'AND base."{PERSON_REPEAT_FIELD}" > 0')
    if filters.get("risk_level"):
        sql_parts.append('AND base."风险等级" = %s')
        params.append(filters["risk_level"])
    if filters.get("min_joint_bids") not in (None, ""):
        sql_parts.append('AND base."共同参与次数" >= %s')
        params.append(int(filters["min_joint_bids"]))

    order_by = 'base."共同参与次数" DESC'
    if filters.get("risk_dimension") == "电话邮箱相同":
        order_by = 'base."不同投标人电话邮箱相同" DESC, base."共同参与次数" DESC'
    elif filters.get("risk_dimension") == "投标人完全一致":
        order_by = 'base."不同标段投标人完全一致" DESC, base."共同参与次数" DESC'
    elif filters.get("risk_dimension") == "硬件或IP重复":
        order_by = 'base."不同投标人硬件及ip地址一致" DESC, base."共同参与次数" DESC'
    elif filters.get("risk_dimension") == "保证金同一账户转出":
        order_by = 'base."投标保证金从同一账户转出" DESC, base."共同参与次数" DESC'
    elif filters.get("risk_dimension") == "人员重复":
        order_by = f'base."{PERSON_REPEAT_FIELD}" DESC, base."共同参与次数" DESC'
    sql_parts.append(f"ORDER BY {order_by}")
    sql_parts.append("LIMIT %s")
    params.append(int(filters.get("limit", 50)))
    return "\n".join(sql_parts), params


def build_bid_collusion_detail_query(filters: dict | None = None) -> tuple[str, list[Any]]:
    filters = {
        key: value for key, value in (filters or {}).items() if value not in (None, "", [])
    }
    sql_parts = [
        "SELECT *",
        "FROM (",
        BID_COLLUSION_DETAIL_BASE_SQL,
        ') AS detail',
        "WHERE 1=1",
    ]
    params: list[Any] = []
    sql_parts.append('AND detail."投标人1" = %s')
    params.append(filters["tbrmc1"])
    sql_parts.append('AND detail."投标人2" = %s')
    params.append(filters["tbrmc2"])
    sql_parts.append('AND detail."疑点类型" = %s')
    params.append(filters["ydlx"])
    if filters.get("zbnd"):
        sql_parts.append('AND detail."招标年度" = %s')
        params.append(filters["zbnd"])
    if filters.get("zbdw"):
        sql_parts.append('AND detail."招标单位" = %s')
        params.append(filters["zbdw"])
    if filters.get("xmmc"):
        sql_parts.append('AND detail."项目名称" ILIKE %s')
        params.append(f'%{filters["xmmc"]}%')
    if filters.get("bdmc"):
        sql_parts.append('AND detail."标段名称" ILIKE %s')
        params.append(f'%{filters["bdmc"]}%')
    sql_parts.append('ORDER BY detail."抽取日期" DESC, detail."项目名称" ASC, detail."标段名称" ASC')
    sql_parts.append("LIMIT %s")
    params.append(int(filters.get("limit", 100)))
    return "\n".join(sql_parts), params


def _as_rows(columns, records):
    return [dict(zip(columns, row)) for row in records]


def _log_bid_collusion_sql(tool_name: str, sql: str, params: list[Any]) -> None:
    _log_sql_execution(tool_name, sql, params)


def _summarize_bid_collusion_rows(rows: list[dict]) -> str:
    if not rows:
        return "未查询到满足条件的串围标风险记录。"
    top_row = rows[0]
    return (
        f"共查询到 {len(rows)} 组疑似关系，最高风险组合为 {top_row['投标人1']} 与 "
        f"{top_row['投标人2']}，风险等级 {top_row['风险等级']}，共同参与次数 "
        f"{top_row['共同参与次数']}。"
    )


def _summarize_bid_collusion_rows_with_filters(rows: list[dict], filters: dict) -> str:
    if rows:
        return _summarize_bid_collusion_rows(rows)
    risk_dimension = (filters.get("risk_dimension") or "").strip()
    if risk_dimension:
        return f"在当前过滤条件下未查询到“{risk_dimension}”维度的串围标风险记录。应直接报告未命中，不要切换到其他风险维度补充。"
    return "未查询到满足条件的串围标风险记录。"


class QueryBidCollusionRiskTool(BaseTool):
    name = "query_bid_collusion_risk"
    description = "旧串围标风险查询工具。主智能体规划阶段不要直接调用；仅在兼容旧链路或执行链明确需要时使用。"
    input_schema = {
        "type": "object",
        "properties": {
            "zbnd": {"type": "string", "description": "招标年度，例如 2025"},
            "zbdw": {"type": "string", "description": "招标单位名称，例如 国家管网集团"},
            "xmmc": {"type": "string", "description": "项目名称，支持模糊匹配"},
            "bdmc": {"type": "string", "description": "标段名称，支持模糊匹配"},
            "date_from": {"type": "string", "description": "开标日期起始，格式 YYYY-MM-DD"},
            "date_to": {"type": "string", "description": "开标日期结束，格式 YYYY-MM-DD"},
            "tbrmc": {"type": "string", "description": "投标人名称，会同时匹配投标人1和投标人2"},
            "risk_dimension": {
                "type": "string",
                "description": "风险维度，只允许 电话邮箱相同、投标人完全一致、硬件或IP重复、保证金同一账户转出、人员重复",
            },
            "risk_level": {"type": "string", "description": "风险等级，可选 高、中、低"},
            "min_joint_bids": {"type": "integer", "description": "最低共同参与次数"},
            "limit": {"type": "integer", "description": "返回结果数量上限"},
        },
        "required": ["zbnd", "zbdw", "risk_dimension"],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        filters = {
            "zbnd": str(arguments.get("zbnd", "")).strip(),
            "zbdw": str(arguments.get("zbdw", "")).strip(),
            "xmmc": str(arguments.get("xmmc", "")).strip(),
            "bdmc": str(arguments.get("bdmc", "")).strip(),
            "date_from": str(arguments.get("date_from", "")).strip(),
            "date_to": str(arguments.get("date_to", "")).strip(),
            "tbrmc": str(arguments.get("tbrmc", "")).strip(),
            "risk_dimension": str(arguments.get("risk_dimension", "")).strip(),
            "risk_level": str(arguments.get("risk_level", "")).strip(),
            "min_joint_bids": arguments.get("min_joint_bids") or None,
            "limit": arguments.get("limit") or 50,
        }
        sql, params = build_bid_collusion_query(filters)
        _log_bid_collusion_sql(self.name, sql, params)
        conn = self.connection_factory()
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                records = cur.fetchall()
                columns = [item[0] for item in cur.description]
            finally:
                cur.close()
        finally:
            conn.close()
        rows = _as_rows(columns, records)
        return {
            "tool": self.name,
            "ok": True,
            "filters": {key: value for key, value in filters.items() if value not in (None, "", [])},
            "row_count": len(rows),
            "rows": rows,
            "summary": _summarize_bid_collusion_rows_with_filters(rows, filters),
        }


class QueryBidCollusionDetailTool(BaseTool):
    name = "query_bid_collusion_detail"
    description = "串围标结果后的详情下钻工具。该工具返回可分页的明细数据集预览和 query_handle，不直接把全部明细塞入上下文；模型可继续调用 query_model_result_table 分页/筛选，或由前端通过查看数据弹窗筛选后再送入上下文。参数由模型结合上下文填写；若无法唯一确定 tbrmc1、tbrmc2、ydlx，则应先最小澄清。ydlx 必须严格使用工具枚举原值：电话邮箱相同、投标人完全一致、硬件或IP重复、保证金同一账户转出、人员重复。"
    input_schema = {
        "type": "object",
        "properties": {
            "tbrmc1": {"type": "string", "description": "投标人1名称"},
            "tbrmc2": {"type": "string", "description": "投标人2名称"},
            "ydlx": {
                "type": "string",
                "description": "异常类型，只允许 电话邮箱相同、投标人完全一致、硬件或IP重复、保证金同一账户转出、人员重复",
            },
            "name": {"type": "string", "description": "招标人名称，例如 国家管网集团"},
            "zbnd": {"type": "string", "description": "招标年度，例如 2025"},
            "zbdw": {"type": "string", "description": "招标单位名称，例如 国家管网集团"},
            "xmmc": {"type": "string", "description": "项目名称，支持模糊匹配"},
            "bdmc": {"type": "string", "description": "标段名称，支持模糊匹配"},
            "limit": {"type": "integer", "description": "预览明细数量上限，默认 5；完整数据通过 query_handle 分页读取"},
        },
        "required": ["tbrmc1", "tbrmc2", "ydlx"],
    }

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or _default_connection_factory

    def execute(self, arguments: dict, context) -> dict:
        ydlx = str(arguments.get("ydlx", "")).strip()
        if ydlx not in allowed_ydlx_values():
            raise ValueError("ydlx 只允许为 电话邮箱相同、投标人完全一致、硬件或IP重复、保证金同一账户转出、人员重复")
        filters = {
            "投标人1": str(arguments.get("tbrmc1", "")).strip(),
            "投标人2": str(arguments.get("tbrmc2", "")).strip(),
            "疑点类型": ydlx,
            "招标人": str(arguments.get("name", "")).strip(),
            "招标年度": str(arguments.get("zbnd", "")).strip(),
            "招标单位": str(arguments.get("zbdw", "")).strip(),
            "项目名称": str(arguments.get("xmmc", "")).strip(),
            "标段名称": str(arguments.get("bdmc", "")).strip(),
        }
        filters = {key: value for key, value in filters.items() if value not in (None, "", [])}
        preview_size = _coerce_limit(arguments.get("limit"), default=5)
        dimension = get_dimension_by_ydlx(ydlx)
        sql_code = str(
            arguments.get("sql_code") or (dimension.detail_sql_code if dimension else "") or "main"
        ).strip() or "main"
        sql_config = _load_model_sql_config(
            self.connection_factory,
            model_id=BID_COLLUSION_DETAIL_MODEL_ID,
            sql_code=sql_code,
        )
        if sql_config is None:
            return {
                "tool": self.name,
                "ok": False,
                "detail_type": ydlx,
                "filters": filters,
                "summary": "串围标详情下钻 SQL 未配置，无法生成明细数据集。",
            }
        schema = _coerce_sql_schema(sql_config.get("sql_schema"))
        rows = _query_configured_sql_page(
            connection_factory=self.connection_factory,
            sql_text=str(sql_config.get("sql_text") or ""),
            schema=schema,
            model_id=BID_COLLUSION_DETAIL_MODEL_ID,
            filters=filters,
            order_by={},
            page=1,
            page_size=preview_size,
        )
        has_more = len(rows) > preview_size
        preview_rows = rows[:preview_size]
        model_row = {
            "model_id": BID_COLLUSION_DETAIL_MODEL_ID,
            "model_name": BID_COLLUSION_DETAIL_MODEL_NAME,
        }
        query_handle = _build_query_handle(model_row, sql_config, {"filters": filters})
        summary = (
            f"已生成 {filters['投标人1']} 与 {filters['投标人2']} 在 {ydlx} 维度下的明细数据集预览，本页 {len(preview_rows)} 条，可继续分页或筛选。"
            if preview_rows
            else f"{filters['投标人1']} 与 {filters['投标人2']} 在 {ydlx} 维度下未查询到明细记录。应明确报告当前维度无明细，不要切换到其他维度补充。"
        )
        return {
            "tool": self.name,
            "ok": True,
            "type": MODEL_RESULT_DATASET_TYPE,
            "artifact_type": MODEL_RESULT_DATASET_TYPE,
            "tool_type": "bid_collusion_detail_dataset",
            "model_name": BID_COLLUSION_DETAIL_MODEL_NAME,
            "model_id": BID_COLLUSION_DETAIL_MODEL_ID,
            "sql_code": sql_config.get("sql_code"),
            "detail_type": ydlx,
            "filters": filters,
            "schema": schema,
            "query_handle": query_handle,
            "preview_count": len(preview_rows),
            "preview_rows": preview_rows,
            "has_more": has_more,
            "row_count": len(preview_rows),
            "summary": summary,
        }


class QueryBidCollusionRiskDetailTool(QueryBidCollusionDetailTool):
    name = "query_bid_collusion_risk_detail"
    description = "串围标风险下钻明细工具。主智能体规划阶段不要直接调用；仅在详情分析阶段明确需要时使用。"


TOOL_REGISTRY: dict[str, BaseTool] = {
    "query_bid_collusion_risk": QueryBidCollusionRiskTool(connection_factory=lambda: None),  # placeholder for schema inspection
    "query_bid_collusion_detail": QueryBidCollusionDetailTool(connection_factory=lambda: None),  # placeholder for schema inspection
    "query_bid_collusion_risk_detail": QueryBidCollusionRiskDetailTool(connection_factory=lambda: None),  # placeholder for schema inspection
}

SKILL_TOOLCHAINS = {
    "bid-collusion-execution": {
        "candidate_tools": ["query_bid_collusion_risk", "query_bid_collusion_detail", "query_bid_collusion_risk_detail"],
        "paths": [
            {
                "name": "risk_screening",
                "tools": ["query_bid_collusion_risk"],
                "recommended": ["zbnd", "zbdw", "risk_dimension", "risk_level"],
            },
            {
                "name": "detail_drilldown",
                "tools": ["query_bid_collusion_risk_detail"],
                "recommended": ["zbnd", "zbdw", "xmmc", "bdmc"],
            },
        ],
    }
}

OUTPUT_HINTS = {
    "摘要": ["摘要", "概览", "结论", "先给摘要", "先给结论"],
    "明细": ["明细", "详情", "展开", "表格", "证据", "报告"],
}
DETAIL_HINTS = OUTPUT_HINTS["明细"] + list(allowed_ydlx_values())


def _tool_schema(tool_obj: BaseTool) -> dict[str, Any]:
    schema = tool_obj.input_schema
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    normalized_parameters: dict[str, dict[str, Any]] = {}
    for name, meta in properties.items():
        if not isinstance(meta, dict):
            meta = {}
        normalized_parameters[name] = {
            "description": str(meta.get("description", "")),
            "required": name in required,
            "default": meta.get("default"),
            "annotation": str(meta.get("type", "string")),
        }
    return {"required": list(required), "parameters": normalized_parameters}


def _combined_text(user_query: str, history_summary: str) -> str:
    return "\n".join(part for part in [history_summary.strip(), user_query.strip()] if part)


def _extract_year(text: str) -> str:
    match = re.search(r"(20\d{2})年?", text)
    return match.group(1) if match else ""


def _extract_output_form(text: str) -> str:
    for label, phrases in OUTPUT_HINTS.items():
        if any(phrase in text for phrase in phrases):
            return label
    return ""


def _extract_risk_dimension(text: str) -> str:
    for item in allowed_ydlx_values():
        if item in text:
            return item
    return ""


def _extract_project(text: str) -> str:
    cleaned = _normalize_org_phrase(text)
    prefixed_match = re.search(
        r"(?:[\u4e00-\u9fa5A-Za-z0-9]+(?:集团|分公司|公司|单位))([\u4e00-\u9fa5A-Za-z0-9]+项目)",
        cleaned,
    )
    if prefixed_match:
        return _normalize_org_phrase(prefixed_match.group(1))
    match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]+项目)", cleaned)
    return _normalize_org_phrase(match.group(1)) if match else ""


def _extract_unit(text: str) -> str:
    confirmed_match = re.search(r"准确(?:单位)?名称为[\"“']?([^\"”']+)[\"”']?", text)
    if confirmed_match:
        return _normalize_org_phrase(confirmed_match.group(1))
    explicit_match = re.search(
        r"(?:主体|单位|招标单位)(?:是|为)[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9]+(?:集团|分公司|公司|单位))",
        text,
    )
    if explicit_match:
        return _normalize_org_phrase(explicit_match.group(1))
    cleaned = _normalize_org_phrase(text)
    prioritized_patterns = [
        r"([\u4e00-\u9fa5A-Za-z0-9]+集团)",
        r"([\u4e00-\u9fa5A-Za-z0-9]+分公司)",
        r"([\u4e00-\u9fa5A-Za-z0-9]+公司)",
        r"([\u4e00-\u9fa5A-Za-z0-9]+单位)",
    ]
    for pattern in prioritized_patterns:
        match = re.search(pattern, cleaned)
        if match:
            return _normalize_org_phrase(match.group(1))
    return ""


def _extract_bidder_pair(text: str) -> tuple[str, str]:
    match = re.search(
        r"([\u4e00-\u9fa5A-Za-z0-9（）()]+(?:公司|集团|中心|厂|院|所|局))\s*(?:和|与)\s*([\u4e00-\u9fa5A-Za-z0-9（）()]+(?:公司|集团|中心|厂|院|所|局))",
        text,
    )
    if not match:
        return "", ""
    return _normalize_bidder_name(match.group(1)), _normalize_bidder_name(match.group(2))


def _extract_bidder(text: str) -> str:
    if not any(keyword in text for keyword in ("投标人", "供应商")):
        return ""
    pair = _extract_bidder_pair(text)
    if pair[0] and pair[1]:
        return ""
    match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9（）()]+(?:公司|集团|中心|厂|院|所|局))", text)
    return _normalize_bidder_name(match.group(1)) if match else ""


def _normalize_bidder_name(value: str) -> str:
    return _normalize_org_phrase(value)


def _normalize_org_phrase(value: str) -> str:
    normalized = value.strip(" ，。；：:,.")
    normalized = re.sub(
        r"^((请帮我|帮我|请|展开|查看|分析下|分析一下|分析|查询|排查|识别|确认|开始执行|先看|给我|对|用户最初想分析|我选择))+",
        "",
        normalized,
    )
    normalized = re.sub(r"^(主体|单位|招标单位)(?:是|为)[:：]?", "", normalized)
    normalized = re.sub(r"^\d{4}年", "", normalized)
    normalized = re.sub(r"(的串围标风险.*)$", "", normalized)
    normalized = re.sub(r"(串围标风险.*)$", "", normalized)
    normalized = re.sub(r"(并给我.*)$", "", normalized)
    normalized = re.sub(r"(先看.*)$", "", normalized)
    normalized = re.sub(r"(维度.*)$", "", normalized)
    return normalized.strip(" ，。；：:,.")


def _extract_param(param_name: str, text: str) -> Any:
    if param_name == "zbnd":
        return _extract_year(text)
    if param_name == "zbdw":
        return _extract_unit(text)
    if param_name == "xmmc":
        return _extract_project(text)
    if param_name == "tbrmc":
        return _extract_bidder(text)
    if param_name == "tbrmc1":
        return _extract_bidder_pair(text)[0]
    if param_name == "tbrmc2":
        return _extract_bidder_pair(text)[1]
    if param_name in {"ydlx", "risk_dimension"}:
        return _extract_risk_dimension(text)
    if param_name in {"date_from", "date_to"}:
        return ""
    if param_name == "risk_level":
        for label in ("高", "中", "低"):
            if f"{label}风险" in text or f"风险等级{label}" in text:
                return label
        return ""
    if param_name == "limit":
        return None
    return ""


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return value


def _resolve_param_value(
    *,
    param_name: str,
    combined_text: str,
    confirmed_params: dict[str, Any],
    candidate_params: dict[str, Any],
) -> Any:
    if param_name in confirmed_params and confirmed_params[param_name] not in ("", None):
        return confirmed_params[param_name]
    if param_name in candidate_params and candidate_params[param_name] not in ("", None):
        return candidate_params[param_name]
    return _extract_param(param_name, combined_text)


def _selected_path(user_query: str, extracted: dict[str, Any]) -> dict[str, Any]:
    if any(keyword in user_query for keyword in DETAIL_HINTS):
        if extracted.get("tbrmc1") and extracted.get("tbrmc2") and extracted.get("ydlx"):
            return SKILL_TOOLCHAINS["bid-collusion-execution"]["paths"][1]
    return SKILL_TOOLCHAINS["bid-collusion-execution"]["paths"][0]


def _strict_clarification_message(
    path: dict[str, Any],
    missing_required_params: list[dict[str, str]],
    tool_requirements: dict[str, Any],
) -> str:
    if not missing_required_params:
        return ""
    selected_tool = path["tools"][0]
    if selected_tool == "query_bid_collusion_risk":
        schema = tool_requirements[selected_tool]["parameters"]
        lines = [
            "当前要执行的是 `query_bid_collusion_risk` 风险筛查。",
            "请补充以下必填参数，我才能开始分析：",
        ]
        for item in missing_required_params:
            param_name = item["param"]
            lines.append(f"- `{param_name}`：{schema[param_name]['description']}")
        optional_params = [
            name for name, meta in schema.items() if not meta["required"] and name not in {"limit"}
        ]
        if optional_params:
            lines.append("可选补充参数包括：" + "、".join(f"`{item}`" for item in optional_params) + "。")
        lines.append("不要补充当前工具未使用的数据源或证据材料。")
        return "\n".join(lines)
    lines = [f"当前要执行的是 `{selected_tool}`。", "请补充以下缺失参数："]
    for item in missing_required_params:
        lines.append(f"- `{item['param']}`：{item['reason']}")
    lines.append("不要补充当前工具未使用的数据源或证据材料。")
    return "\n".join(lines)


def _path_missing_requirements(
    path: dict[str, Any],
    extracted: dict[str, Any],
    tool_schemas: dict[str, Any],
    *,
    validation_results: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    validation_results = validation_results or {}
    for tool_name in path["tools"]:
        schema = tool_schemas[tool_name]
        validation_first: list[str] = []
        for param_name in schema["required"]:
            validation = validation_results.get(param_name)
            if isinstance(validation, dict) and validation.get("ok") is False:
                validation_first.append(param_name)
        handled = set(validation_first)
        for param_name in validation_first:
            validation = validation_results.get(param_name)
            missing.append(
                {
                    "tool": tool_name,
                    "param": param_name,
                    "reason": str(validation.get("reason") or "参数未通过校验"),
                }
            )
        for param_name in schema["required"]:
            if param_name in handled:
                continue
            if not extracted.get(param_name):
                missing.append(
                    {
                        "tool": tool_name,
                        "param": param_name,
                        "reason": schema["parameters"][param_name]["description"] or "工具必填参数缺失",
                    }
                )
    return missing


def _plan_skeleton(path: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": path["name"],
        "selected_tools": path["tools"],
        "known_params": {key: value for key, value in extracted.items() if value not in ("", None)},
        "suggested_steps": [
            "主智能体确认当前上下文与执行边界",
            "向用户展示任务编排并等待确认",
            "确认后派发采购子智能体执行选定工具链",
        ],
    }


class PlanningTool(BaseTool):
    name = "planning_tool"
    description = "根据当前采购专项 skill、工具参数和上下文生成最小澄清与任务编排骨架。"
    input_schema = {
        "type": "object",
        "properties": {
            "user_query": {"type": "string", "description": "用户当前输入的原始问题。"},
            "skill_name": {"type": "string", "description": "准备进入的 skill 名称，例如 bid-collusion-execution。"},
            "history_summary": {"type": "string", "description": "历史上下文摘要。"},
            "confirmed_params": {
                "type": "object",
                "description": "已经通过用户确认或专用校验工具确认的参数，优先级最高。",
            },
            "candidate_params": {
                "type": "object",
                "description": "模型从当前问题中理解出的候选参数，优先级低于 confirmed_params。",
            },
            "validation_results": {
                "type": "object",
                "description": "参数校验结果，键为参数名，值可包含 ok/reason 等字段。",
            },
        },
        "required": ["user_query", "skill_name"],
    }

    def execute(self, arguments: dict, context) -> dict:
        user_query = str(arguments.get("user_query", "")).strip()
        skill_name = str(arguments.get("skill_name", "")).strip()
        history_summary = str(arguments.get("history_summary", "")).strip()
        confirmed_params = _coerce_mapping(arguments.get("confirmed_params"))
        candidate_params = _coerce_mapping(arguments.get("candidate_params"))
        validation_results = _coerce_mapping(arguments.get("validation_results"))
        skill_config = SKILL_TOOLCHAINS.get(skill_name)
        if not skill_config:
            return {
                "selected_skill": skill_name,
                "candidate_tools": [],
                "selected_tools": [],
                "tool_requirements": {},
                "extracted_params": {},
                "missing_required_params": [],
                "clarification_questions": [],
                "can_plan": False,
                "plan_skeleton": {},
                "summary": f"未找到 skill {skill_name} 对应的规划配置。",
            }
        combined = _combined_text(user_query, history_summary)
        tool_requirements = {
            tool_name: _tool_schema(TOOL_REGISTRY[tool_name])
            for tool_name in skill_config["candidate_tools"]
        }
        extracted: dict[str, Any] = {}
        for schema in tool_requirements.values():
            for param_name in schema["parameters"]:
                extracted.setdefault(
                    param_name,
                    _resolve_param_value(
                        param_name=param_name,
                        combined_text=combined,
                        confirmed_params=confirmed_params,
                        candidate_params=candidate_params,
                    ),
                )
        extracted["output_form"] = _extract_output_form(combined)
        selected_path = _selected_path(combined, extracted)
        missing_required_params = _path_missing_requirements(
            selected_path,
            extracted,
            tool_requirements,
            validation_results=validation_results,
        )
        clarification_questions = [
            f"请补充参数“{item['param']}”对应的信息：{item['reason']}"
            for item in missing_required_params
        ]
        can_plan = not missing_required_params
        return {
            "selected_skill": skill_name,
            "candidate_tools": skill_config["candidate_tools"],
            "selected_tools": selected_path["tools"],
            "tool_requirements": tool_requirements,
            "extracted_params": extracted,
            "confirmed_params": confirmed_params,
            "candidate_params": candidate_params,
            "validation_results": validation_results,
            "missing_required_params": missing_required_params,
            "clarification_questions": clarification_questions,
            "strict_clarification_message": _strict_clarification_message(
                selected_path,
                missing_required_params,
                tool_requirements,
            ),
            "can_plan": can_plan,
            "plan_skeleton": _plan_skeleton(selected_path, extracted) if can_plan else {},
            "summary": "已基于 skill 与工具参数完成规划分析。" if can_plan else "已识别仍需向用户澄清的必要参数。",
        }


query_model_data = QueryModelDataTool()
query_model_catalog_by_risk = QueryModelCatalogByRiskTool()
query_model_parameters = QueryModelParametersTool()
plan_model_execution = PlanModelExecutionTool()
advance_model_workflow = AdvanceModelWorkflowTool()
execute_registered_model = ExecuteRegisteredModelTool()
query_model_result_table = QueryModelResultTableTool()
prepare_bid_collusion_report_context = PrepareBidCollusionReportContextTool()
advance_report_export_workflow = AdvanceReportExportWorkflowTool()
query_group_unit_master = QueryGroupUnitMasterTool()
query_biz_indicator = QueryBizIndicatorTool()
query_bid_collusion_risk = QueryBidCollusionRiskTool()
query_bid_collusion_detail = QueryBidCollusionDetailTool()
query_bid_collusion_risk_detail = QueryBidCollusionRiskDetailTool()
planning_tool = PlanningTool()

TOOL_REGISTRY = {
    "query_bid_collusion_risk": query_bid_collusion_risk,
    "query_bid_collusion_detail": query_bid_collusion_detail,
    "query_bid_collusion_risk_detail": query_bid_collusion_risk_detail,
}


def build_procurement_tools(*, settings: Settings | None = None) -> list[BaseTool]:
    connection_factory = None
    if settings is not None:
        engine = build_engine(settings)
        connection_factory = engine.raw_connection
    # 只把当前可用业务入口暴露给模型：指标查询、目录清单、串围标主链和报告导出。
    # 旧风险查询和旧 planning_tool 保留代码与单测，但不进入默认 wiring。
    return [
        QueryBizIndicatorTool(connection_factory=connection_factory),
        QueryModelCatalogByRiskTool(connection_factory=connection_factory),
        AdvanceModelWorkflowTool(connection_factory=connection_factory),
        ExecuteRegisteredModelTool(connection_factory=connection_factory),
        QueryModelResultTableTool(connection_factory=connection_factory),
        AdvanceReportExportWorkflowTool(connection_factory=connection_factory),
        QueryGroupUnitMasterTool(connection_factory=connection_factory),
        QueryBidCollusionDetailTool(connection_factory=connection_factory),
    ]


def _map_group_unit_row(row: dict[str, Any]) -> dict[str, Any]:
    org_type = row.get("org_type")
    unit_status = row.get("unit_status")
    tags = [str(item) for item in [org_type, unit_status] if isinstance(item, str) and item]
    return {
        "id": int(row["unit_id"]) if str(row.get("unit_id", "")).isdigit() else row.get("unit_id"),
        "name": row.get("unit_name"),
        "code": row.get("unit_code"),
        "tags": tags,
        "parent_id": (
            int(row["parent_unit_id"])
            if str(row.get("parent_unit_id", "")).isdigit()
            else row.get("parent_unit_id")
        ),
        "parent_name": row.get("parent_unit_name"),
    }


def _build_group_unit_dataset_payload(
    *,
    rows: list[dict[str, Any]],
    query_text: str,
    match_type: str,
    page_size: int | None = None,
) -> dict[str, Any]:
    return {
        "type": MODEL_RESULT_DATASET_TYPE,
        "artifact_type": MODEL_RESULT_DATASET_TYPE,
        "tool_type": "table_query",
        "dataset_title": "集团单位主数据",
        "schema": {
            "columns": [
                {"name": "code", "label": "单位编码"},
                {"name": "name", "label": "单位名称"},
                {"name": "parent_name", "label": "上级单位"},
                {"name": "tags", "label": "标签"},
            ],
            "filterable_columns": ["code", "name", "parent_name", "tags"],
            "exact_filter_columns": ["code", "name"],
        },
        "query_handle": {
            "kind": "group_unit_master",
            "query_text": query_text,
            "match_type": match_type,
        },
        "page": 1,
        "page_size": max(page_size, 1) if page_size is not None else max(len(rows), 1),
        "has_more": False,
        "row_count": len(rows),
        "rows": rows,
    }


def _group_unit_table_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": item.get("code"),
        "name": item.get("name"),
        "parent_name": item.get("parent_name"),
        "tags": item.get("tags") or [],
    }


def _filter_group_unit_items(
    items: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    active_filters = {
        str(key): value
        for key, value in filters.items()
        if key in {"code", "name", "parent_name", "tags"} and value not in (None, "", [])
    }
    if not active_filters:
        return items

    def _matches(item: dict[str, Any]) -> bool:
        for key, expected in active_filters.items():
            actual = item.get(key)
            if isinstance(actual, list):
                actual_text = " ".join(str(value) for value in actual)
            else:
                actual_text = str(actual or "")
            if str(expected).lower() not in actual_text.lower():
                return False
        return True

    return [item for item in items if _matches(item)]


def _map_biz_indicator_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("indicator_id"),
        "name": row.get("indicator_name"),
        "value": row.get("indicator_value"),
        "stat_date": str(row["stat_date"]) if row.get("stat_date") is not None else None,
        "create_time": str(row["create_time"]) if row.get("create_time") is not None else None,
    }


def _model_catalog_exact_result(
    *,
    query_text: str,
    risk_tag: str,
    row: dict[str, Any],
    summary_prefix: str,
) -> dict[str, Any]:
    matched = _map_model_catalog_row(row)
    return {
        "ok": True,
        "query_text": query_text,
        "risk_tag": risk_tag,
        "match_type": "exact",
        "need_confirmation": False,
        "matched_item": matched,
        "candidates": [],
        "items": [],
        "total_returned": 1,
        "summary": f"{summary_prefix}：{matched['name']}。",
    }


def _model_catalog_candidates_result(
    *,
    query_text: str,
    risk_tag: str,
    rows: list[dict[str, Any]],
    none_summary: str,
) -> dict[str, Any]:
    candidates = [_map_model_catalog_row(row) for row in rows]
    return {
        "ok": True,
        "query_text": query_text,
        "risk_tag": risk_tag,
        "match_type": "fuzzy" if candidates else "none",
        "need_confirmation": bool(candidates),
        "matched_item": None,
        "candidates": candidates,
        "items": [],
        "total_returned": len(candidates),
        "summary": (
            f"未精确匹配到模型清单，按模糊匹配返回 {len(candidates)} 个候选项供确认。"
            if candidates
            else none_summary
        ),
    }


def _model_catalog_items_result(
    *,
    query_text: str,
    risk_tag: str,
    rows: list[dict[str, Any]],
    match_type: str,
    summary: str,
) -> dict[str, Any]:
    items = [_map_model_catalog_row(row) for row in rows]
    return {
        "ok": True,
        "query_text": query_text,
        "risk_tag": risk_tag,
        "match_type": match_type,
        "need_confirmation": False,
        "matched_item": None,
        "candidates": [],
        "items": items,
        "total_returned": len(items),
        "summary": summary,
    }


def _map_model_catalog_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]) if str(row.get("id", "")).isdigit() else row.get("id"),
        "model_id": row.get("model_id"),
        "name": row.get("model_name"),
        "model_name": row.get("model_name"),
        "model_type": row.get("model_type"),
        "result_table": row.get("result_table"),
        "executor_code": row.get("executor_code"),
        "risk_tag": row.get("risk_tag"),
        "create_time": str(row["create_time"]) if row.get("create_time") is not None else None,
        "update_time": str(row["update_time"]) if row.get("update_time") is not None else None,
    }


def _map_workflow_catalog_item(item: dict[str, Any]) -> dict[str, Any]:
    # SQL 预览执行链不再依赖 result_table 落库；不要把旧结果表名暴露给模型，避免误导回复。
    return {
        "id": item.get("id"),
        "model_id": item.get("model_id"),
        "name": item.get("name") or item.get("model_name"),
        "model_name": item.get("model_name") or item.get("name"),
        "model_type": item.get("model_type"),
        "executor_code": item.get("executor_code"),
        "risk_tag": item.get("risk_tag"),
    }


def _coerce_report_count(value: Any, *, fallback: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback


def _distribution(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(field)
        if value in (None, ""):
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return [
        {"value": value, "count": count}
        for value, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]


def _numeric_report_value(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else 0.0


def _dimension_totals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals = []
    for field in scene_dimension_fields():
        total = sum(_numeric_report_value(row.get(field)) for row in rows)
        totals.append({"dimension": field, "total": int(total) if total.is_integer() else total})
    return sorted(totals, key=lambda item: _numeric_report_value(item["total"]), reverse=True)


def _resolve_report_dimension(
    query_handle: dict[str, Any],
    model_row: dict[str, Any],
) -> BidCollusionDimensionSpec | None:
    return (
        get_dimension_by_single_model_id(str(query_handle.get("model_id") or ""))
        or get_dimension_by_model_name(str(query_handle.get("model_name") or ""))
        or get_dimension_by_single_model_id(str(model_row.get("model_id") or ""))
        or get_dimension_by_model_name(str(model_row.get("model_name") or ""))
    )


def _build_report_aggregate_from_query_handle(
    *,
    connection_factory: Callable[[], Any],
    query_handle: dict[str, Any],
    top_n: int,
) -> dict[str, Any]:
    model_id = str(query_handle.get("model_id") or "").strip()
    model_name = str(query_handle.get("model_name") or "").strip()
    sql_code = str(query_handle.get("sql_code") or "main").strip() or "main"
    model_row = _load_model_catalog_row(connection_factory, model_name=model_name, model_id=model_id)
    if model_row is None:
        model_row = {"model_id": model_id, "model_name": model_name or model_id}
    sql_config = _load_model_sql_config(
        connection_factory,
        model_id=str(model_row.get("model_id") or model_id),
        sql_code=sql_code,
    )
    if sql_config is None:
        return {}
    schema = _coerce_sql_schema(sql_config.get("sql_schema"))
    filters = _query_handle_filters(query_handle)
    dimension = _resolve_report_dimension(query_handle, model_row)
    report_type = "single_dimension" if dimension is not None else "scene"
    filtered = _build_configured_sql_query(
        sql_text=str(sql_config.get("sql_text") or ""),
        schema=schema,
        model_id=str(model_row.get("model_id") or model_id),
        filters=filters,
    )
    if not filtered["sql"]:
        return {}
    row_count = _fetch_scalar_int(
        connection_factory,
        f"SELECT COUNT(*) AS row_count FROM ({filtered['sql']}) AS report_base",
        list(filtered["params"]),
    )
    risk_distribution = (
        _fetch_group_distribution(
            connection_factory,
            filtered["sql"],
            list(filtered["params"]),
            "风险等级",
        )
        if "风险等级" in _schema_column_names(schema)
        else []
    )
    dimension_totals = _fetch_dimension_totals(
        connection_factory,
        filtered["sql"],
        list(filtered["params"]),
        (dimension.scene_field,) if dimension is not None else scene_dimension_fields(),
    )
    top_rows = _query_configured_sql_page(
        connection_factory=connection_factory,
        sql_text=str(sql_config.get("sql_text") or ""),
        schema=schema,
        model_id=str(model_row.get("model_id") or model_id),
        filters=filters,
        order_by={},
        page=1,
        page_size=top_n,
    )[:top_n]
    return {
        "report_type": report_type,
        "dimension": dimension_to_dict(dimension) if dimension is not None else None,
        "row_count": row_count,
        "risk_level_distribution": risk_distribution,
        "dimension_totals": dimension_totals,
        "top_rows": top_rows,
    }


def _fetch_scalar_int(
    connection_factory: Callable[[], Any],
    sql: str,
    params: list[Any],
) -> int:
    rows = _fetch_rows(connection_factory, sql, params)
    if not rows:
        return 0
    return _coerce_report_count(next(iter(rows[0].values()), 0), fallback=0)


def _fetch_group_distribution(
    connection_factory: Callable[[], Any],
    base_sql: str,
    params: list[Any],
    column: str,
) -> list[dict[str, Any]]:
    rows = _fetch_rows(
        connection_factory,
        (
            f"SELECT {_quote_sql_identifier(column)} AS value, COUNT(*) AS count "
            f"FROM ({base_sql}) AS report_base "
            f"GROUP BY {_quote_sql_identifier(column)} "
            "ORDER BY count DESC"
        ),
        params,
    )
    return [
        {"value": row.get("value"), "count": _coerce_report_count(row.get("count"), fallback=0)}
        for row in rows
        if row.get("value") not in (None, "")
    ]


def _fetch_dimension_totals(
    connection_factory: Callable[[], Any],
    base_sql: str,
    params: list[Any],
    dimensions: tuple[str, ...],
) -> list[dict[str, Any]]:
    select_parts = [
        f"SUM(COALESCE({_quote_sql_identifier(field)}, 0)) AS {_quote_sql_identifier(field)}"
        for field in dimensions
    ]
    rows = _fetch_rows(
        connection_factory,
        f"SELECT {', '.join(select_parts)} FROM ({base_sql}) AS report_base",
        params,
    )
    row = rows[0] if rows else {}
    totals = [
        {"dimension": field, "total": _coerce_report_count(row.get(field), fallback=0)}
        for field in dimensions
    ]
    return sorted(totals, key=lambda item: _numeric_report_value(item["total"]), reverse=True)


def _top_bidder_pairs(rows: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            {"高": 3, "中": 2, "低": 1}.get(str(row.get("风险等级")), 0),
            _numeric_report_value(row.get("共同参与次数")),
        ),
        reverse=True,
    )
    top_pairs = []
    for row in ranked[:limit]:
        evidence = {
            field: row.get(field)
            for field in scene_dimension_fields()
            if _numeric_report_value(row.get(field)) > 0
        }
        top_pairs.append(
            {
                "投标人1": row.get("投标人1"),
                "投标人2": row.get("投标人2"),
                "招标人": row.get("招标人"),
                "招标单位": row.get("招标单位"),
                "招标年度": row.get("招标年度"),
                "风险等级": row.get("风险等级"),
                "共同参与次数": row.get("共同参与次数"),
                "均未中标次数": row.get("均未中标次数"),
                "中标集中度": row.get("中标集中度"),
                "evidence_counts": evidence,
            }
        )
    return top_pairs


def _top_bidder_pairs_for_dimension(
    rows: list[dict[str, Any]],
    *,
    dimension: BidCollusionDimensionSpec,
    limit: int = 10,
) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: _numeric_report_value(row.get(dimension.scene_field)),
        reverse=True,
    )
    top_pairs = []
    for row in ranked[:limit]:
        count = row.get(dimension.scene_field)
        top_pairs.append(
            {
                "投标人1": row.get("投标人1"),
                "投标人2": row.get("投标人2"),
                "招标人": row.get("招标人"),
                "招标单位": row.get("招标单位"),
                "招标年度": row.get("招标年度"),
                "风险等级": row.get("风险等级"),
                "共同参与次数": row.get("共同参与次数") or count,
                "均未中标次数": row.get("均未中标次数"),
                "中标集中度": row.get("中标集中度"),
                "evidence_counts": {dimension.scene_field: count},
            }
        )
    return top_pairs


def _recommended_report_detail_requests(top_pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requests = []
    for pair in top_pairs:
        evidence_counts = _coerce_mapping(pair.get("evidence_counts"))
        for dimension, count in evidence_counts.items():
            dimension_spec = get_dimension_by_scene_field(str(dimension))
            if dimension_spec is None or _numeric_report_value(count) <= 0:
                continue
            requests.append(
                {
                    "tool_name": "query_bid_collusion_detail",
                    "arguments": {
                        "tbrmc1": pair.get("投标人1"),
                        "tbrmc2": pair.get("投标人2"),
                        "ydlx": dimension_spec.ydlx,
                        "name": pair.get("招标人"),
                        "zbnd": pair.get("招标年度"),
                        "zbdw": pair.get("招标单位"),
                    },
                    "reason": f"{pair.get('投标人1')} 与 {pair.get('投标人2')} 在 {dimension_spec.ydlx} 维度命中 {count} 条线索",
                }
            )
            break
        if len(requests) >= 5:
            break
    return requests


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""


def _build_report_scope(
    rows: list[dict[str, Any]],
    filters: dict[str, Any],
    scope_options: dict[str, Any],
    model_result: dict[str, Any],
) -> dict[str, Any]:
    first_row = rows[0] if rows else {}
    return {
        "subject": _first_non_empty(scope_options.get("subject"), filters.get("招标单位"), filters.get("招标人"), first_row.get("招标单位"), first_row.get("招标人")),
        "subject_role": _first_non_empty(scope_options.get("subject_role"), "招标单位" if filters.get("招标单位") else ""),
        "year": _first_non_empty(scope_options.get("year"), filters.get("招标年度"), first_row.get("招标年度")),
        "model_name": _first_non_empty(scope_options.get("model_name"), model_result.get("model_name")),
        "filters": filters,
    }


def _default_bid_collusion_report_title(scope: dict[str, Any]) -> str:
    subject = str(scope.get("subject") or "指定对象").strip()
    year = str(scope.get("year") or "").strip()
    year_text = f"{year}年度" if year else ""
    return f"{subject}{year_text}串围标风险分析报告"


def _invalid_report_context_argument_keys(arguments: dict[str, Any]) -> list[str]:
    invalid: list[str] = []
    for key in ("model_result", "detail_results", "rows", "preview_rows"):
        if key in arguments:
            invalid.append(key)
    report = arguments.get("report")
    if isinstance(report, dict):
        for key in ("text", "content", "markdown", "body", "data", "rows", "preview_rows"):
            if key in report:
                invalid.append(f"report.{key}")
    return invalid


def _summarize_report_details(
    detail_results: list[dict[str, Any]],
    *,
    connection_factory: Callable[[], Any],
) -> dict[str, Any]:
    items = []
    total_rows = 0
    for result in detail_results:
        query_handle = _coerce_mapping(result.get("query_handle"))
        if query_handle:
            aggregate = _build_detail_aggregate_from_query_handle(
                connection_factory=connection_factory,
                query_handle=query_handle,
            )
            if aggregate:
                total_rows += int(aggregate.get("row_count") or 0)
                items.append(aggregate)
                continue
        rows = result.get("rows") if isinstance(result.get("rows"), list) else result.get("preview_rows")
        detail_rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        if not detail_rows:
            continue
        total_rows += len(detail_rows)
        first = detail_rows[0]
        display_rows = detail_rows[:DETAIL_REPORT_ROW_LIMIT]
        display_mode = "full" if len(detail_rows) <= DETAIL_REPORT_ROW_LIMIT else "excerpt"
        items.append(
            {
                "source_tool": result.get("tool_name") or result.get("source_tool") or "query_bid_collusion_detail",
                "row_count": result.get("row_count") or len(detail_rows),
                "display_mode": display_mode,
                "display_limit": DETAIL_REPORT_ROW_LIMIT,
                "display_count": len(display_rows),
                "display_note": (
                    f"已列出全部 {len(detail_rows)} 条明细。"
                    if display_mode == "full"
                    else f"明细共 {len(detail_rows)} 条，正文仅摘录前 {len(display_rows)} 条。"
                ),
                "投标人1": first.get("投标人1") or first.get("tbrmc1"),
                "投标人2": first.get("投标人2") or first.get("tbrmc2"),
                "疑点类型": first.get("疑点类型") or first.get("ydlx"),
                "coverage": "provided_rows_only",
                "representative_rows": display_rows,
            }
        )
    return {
        "row_count": total_rows,
        "items": items,
    }


def _build_detail_aggregate_from_query_handle(
    *,
    connection_factory: Callable[[], Any],
    query_handle: dict[str, Any],
) -> dict[str, Any]:
    model_id = str(query_handle.get("model_id") or "").strip()
    model_name = str(query_handle.get("model_name") or "").strip()
    sql_code = str(query_handle.get("sql_code") or "main").strip() or "main"
    model_row = _load_model_catalog_row(connection_factory, model_name=model_name, model_id=model_id)
    if model_row is None:
        model_row = {"model_id": model_id, "model_name": model_name or model_id}
    sql_config = _load_model_sql_config(
        connection_factory,
        model_id=str(model_row.get("model_id") or model_id),
        sql_code=sql_code,
    )
    if sql_config is None:
        return {}
    schema = _coerce_sql_schema(sql_config.get("sql_schema"))
    filters = _query_handle_filters(query_handle)
    filtered = _build_configured_sql_query(
        sql_text=str(sql_config.get("sql_text") or ""),
        schema=schema,
        model_id=str(model_row.get("model_id") or model_id),
        filters=filters,
    )
    if not filtered["sql"]:
        return {}
    row_count = _fetch_scalar_int(
        connection_factory,
        f"SELECT COUNT(*) AS row_count FROM ({filtered['sql']}) AS detail_base",
        list(filtered["params"]),
    )
    group_fields = [
        field
        for field in ("疑点类型", "项目名称", "标段名称", "招标人", "招标单位", "招标年度")
        if field in _schema_column_names(schema)
    ]
    distributions = {
        field: _fetch_group_distribution(
            connection_factory,
            filtered["sql"],
            list(filtered["params"]),
            field,
        )
        for field in group_fields
    }
    representative_rows = _query_configured_sql_page(
        connection_factory=connection_factory,
        sql_text=str(sql_config.get("sql_text") or ""),
        schema=schema,
        model_id=str(model_row.get("model_id") or model_id),
        filters=filters,
        order_by={},
        page=1,
        page_size=DETAIL_REPORT_ROW_LIMIT,
    )[:DETAIL_REPORT_ROW_LIMIT]
    first = representative_rows[0] if representative_rows else {}
    display_mode = "full" if row_count <= DETAIL_REPORT_ROW_LIMIT else "excerpt"
    display_note = (
        f"已列出全部 {row_count} 条明细。"
        if display_mode == "full"
        else f"明细共 {row_count} 条，正文仅摘录前 {len(representative_rows)} 条。"
    )
    return {
        "source_tool": "query_bid_collusion_detail",
        "coverage": "full_detail_aggregate",
        "row_count": row_count,
        "display_mode": display_mode,
        "display_limit": DETAIL_REPORT_ROW_LIMIT,
        "display_count": len(representative_rows),
        "display_note": display_note,
        "filters": filters,
        "投标人1": first.get("投标人1") or filters.get("投标人1"),
        "投标人2": first.get("投标人2") or filters.get("投标人2"),
        "疑点类型": first.get("疑点类型") or filters.get("疑点类型"),
        "distributions": distributions,
        "representative_rows": representative_rows,
    }


def _report_limitations(
    warnings: list[str],
    model_result: dict[str, Any],
    detail_context: dict[str, Any],
) -> list[str]:
    limitations = list(warnings)
    limitations.append("模型结果集可能为大数据量结果，报告总体与详情统计由后端按筛选条件聚合，正文仅摘录关键证据明细用于说明。")
    if not detail_context.get("items"):
        limitations.append("当前未纳入原始投标文件、工商股权、人员关系等外部核查材料。")
    return limitations


def _map_model_parameter_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_name": row.get("model_name"),
        "param_name": row.get("param_name"),
        "required": bool(row.get("required")),
        "display_order": int(row["display_order"]) if str(row.get("display_order", "")).isdigit() else row.get("display_order"),
        "validator_tool": row.get("validator_tool"),
    }


def _build_model_param_clarification_message(missing_required_params: list[dict[str, str]]) -> str:
    if not missing_required_params:
        return ""
    lines = [
        "当前已完成模型选择，但还缺少以下必填参数：",
    ]
    for item in missing_required_params:
        param_name = item.get("param_name", "")
        reason = item.get("reason", "")
        lines.append(f"- `{param_name}`：{reason}")
    return "\n".join(lines)


def _infer_procurement_risk_tag(user_query: str, history_summary: str = "") -> str:
    combined = _combined_text(user_query, history_summary)
    if any(
        keyword in combined
        for keyword in (
            "串围标",
            "串标",
            "围标",
            "电话邮箱相同",
            "人员重复",
            "硬件或IP重复",
            "保证金同一账户转出",
            "投标人完全一致",
        )
    ):
        return "串围标"
    return ""


def _select_catalog_model(
    *,
    catalog_items: list[dict[str, Any]],
    explicit_model_name: str,
    user_query: str,
) -> dict[str, Any] | None:
    if explicit_model_name:
        for item in catalog_items:
            if str(item.get("model_name") or "").strip() == explicit_model_name:
                return item
    normalized_query = _normalize_model_phrase(str(user_query or "").strip())
    for item in catalog_items:
        model_name = str(item.get("model_name") or "").strip()
        if model_name and normalized_query == model_name:
            return item
    return None


def _suggest_catalog_models(
    *,
    catalog_items: list[dict[str, Any]],
    explicit_model_name: str,
    user_query: str,
) -> list[dict[str, Any]]:
    probe = _normalize_model_phrase(explicit_model_name or user_query)
    if not probe:
        return []
    suggestions: list[dict[str, Any]] = []
    for item in catalog_items:
        model_name = str(item.get("model_name") or "").strip()
        if not model_name:
            continue
        if model_name in probe or probe in model_name:
            suggestions.append(item)
    return suggestions


def _remove_model_mentions(text: str, model_names: list[str]) -> str:
    normalized = str(text or "")
    for model_name in model_names:
        if not model_name:
            continue
        normalized = normalized.replace(model_name, " ")
    return re.sub(r"\s+", " ", normalized).strip()


def _resolve_model_param_value(
    *,
    param_name: str,
    combined_text: str,
    confirmed_params: dict[str, Any],
    candidate_params: dict[str, Any],
    validation_results: dict[str, Any],
    validator_tool: str,
    allow_text_extraction: bool = True,
) -> Any:
    if allow_text_extraction:
        extracted_value = _extract_param(param_name, combined_text)
        if extracted_value not in ("", None):
            return extracted_value
    validation = validation_results.get(param_name)
    if validator_tool:
        matched_item = validation.get("matched_item") if isinstance(validation, dict) else None
        if isinstance(matched_item, dict) and matched_item.get("name"):
            return matched_item["name"]
        confirmed_value = confirmed_params.get(param_name)
        if confirmed_value not in ("", None):
            return confirmed_value
        candidate_value = candidate_params.get(param_name)
        if candidate_value not in ("", None):
            return candidate_value
    if not allow_text_extraction:
        confirmed_value = confirmed_params.get(param_name)
        if confirmed_value not in ("", None):
            return confirmed_value
        candidate_value = candidate_params.get(param_name)
        if candidate_value not in ("", None):
            return candidate_value
    return "" if allow_text_extraction else None


def _extract_workflow_params(
    *,
    parameter_items: list[dict[str, Any]],
    combined_text: str,
    confirmed_params: dict[str, Any],
    candidate_params: dict[str, Any],
    validation_results: dict[str, Any],
) -> dict[str, Any]:
    extracted: dict[str, Any] = {}
    for item in parameter_items:
        param_name = str(item.get("param_name") or "").strip()
        if not param_name:
            continue
        extracted[param_name] = _resolve_model_param_value(
            param_name=param_name,
            combined_text=combined_text,
            confirmed_params=confirmed_params,
            candidate_params=candidate_params,
            validation_results=validation_results,
            validator_tool=str(item.get("validator_tool") or "").strip(),
            allow_text_extraction=False,
        )
    return extracted


def _collect_pending_validations(
    *,
    parameter_items: list[dict[str, Any]],
    extracted_params: dict[str, Any],
    validation_results: dict[str, Any],
) -> list[dict[str, str]]:
    pending: list[dict[str, str]] = []
    for item in parameter_items:
        param_name = str(item.get("param_name") or "").strip()
        validator_tool = str(item.get("validator_tool") or "").strip()
        if not param_name or not validator_tool:
            continue
        if extracted_params.get(param_name) in ("", None):
            continue
        validation = validation_results.get(param_name)
        if isinstance(validation, dict) and validation.get("ok") is not None:
            continue
        pending.append(
            {
                "param_name": param_name,
                "validator_tool": validator_tool,
                "candidate_value": str(extracted_params.get(param_name) or ""),
                "reason": f"参数 {param_name} 需要先通过 {validator_tool} 校验。",
            }
        )
    return pending


def _merge_validation_results_from_cache(
    *,
    context,
    parameter_items: list[dict[str, Any]],
    extracted_params: dict[str, Any],
    validation_results: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(validation_results)
    for item in parameter_items:
        param_name = str(item.get("param_name") or "").strip()
        validator_tool = str(item.get("validator_tool") or "").strip()
        if not param_name or not validator_tool or param_name in merged:
            continue
        candidate_value = str(extracted_params.get(param_name) or "").strip()
        cached = _load_cached_validator_result(context, validator_tool, candidate_value)
        if isinstance(cached, dict):
            merged[param_name] = cached
    return merged


def _collect_workflow_missing_params(
    *,
    parameter_items: list[dict[str, Any]],
    extracted_params: dict[str, Any],
    validation_results: dict[str, Any],
) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for item in parameter_items:
        param_name = str(item.get("param_name") or "").strip()
        if not param_name:
            continue
        validation = validation_results.get(param_name)
        if isinstance(validation, dict) and validation.get("ok") is False:
            missing.append(
                {
                    "param_name": param_name,
                    "reason": str(validation.get("reason") or f"参数 {param_name} 未通过校验"),
                }
            )
            continue
        if bool(item.get("required")) and extracted_params.get(param_name) in ("", None):
            missing.append(
                {
                    "param_name": param_name,
                    "reason": f"模型执行缺少必填参数 {param_name}",
                }
            )
    return missing


def _build_model_selection_message(
    catalog_items: list[dict[str, Any]],
    risk_tag: str,
    suggested_models: list[dict[str, Any]] | None = None,
) -> str:
    if not catalog_items:
        return f"未找到风险标签“{risk_tag}”对应的模型，请先确认风险标签。"
    lines = [f"已找到风险标签“{risk_tag}”对应的模型，请先明确选择其中一个模型："]
    suggestions = suggested_models or []
    if suggestions:
        lines.append("你当前填写的模型名称没有精确匹配到目录，请先确认是否要选择以下候选模型：")
        for item in suggestions:
            model_name = str(item.get("model_name") or "")
            model_type = str(item.get("model_type") or "")
            lines.append(f"- `{model_name}`（{model_type or '未标注类型'}）")
        lines.append("请让用户明确确认模型名称是否正确。用户只回复模型名称只代表选择模型，下一步必须再次调用 advance_model_workflow 推进参数确认，确认前不要进入执行。")
        return "\n".join(lines)
    for item in catalog_items:
        model_name = str(item.get("model_name") or "")
        model_type = str(item.get("model_type") or "")
        lines.append(f"- `{model_name}`（{model_type or '未标注类型'}）")
    lines.append("在用户明确选择模型前，不进入参数澄清或执行；用户只回复模型名称只代表选择模型，下一步必须再次调用 advance_model_workflow，不得直接调用 execute_registered_model。")
    return "\n".join(lines)


def _build_validation_message(pending_validations: list[dict[str, str]]) -> str:
    lines = ["当前模型参数已提取到候选值，但还需要先完成以下校验："]
    for item in pending_validations:
        lines.append(
            f"- `{item['param_name']}`：候选值 `{item['candidate_value']}`，请调用 `{item['validator_tool']}` 校验。"
        )
    return "\n".join(lines)


def _build_execution_brief(
    *,
    selected_model: dict[str, Any],
    extracted_params: dict[str, Any],
    subject_role: str = "",
) -> dict[str, Any]:
    execution_params = _build_execution_params(extracted_params, subject_role)
    return {
        "model_name": selected_model.get("model_name"),
        "model_type": selected_model.get("model_type"),
        "executor_code": selected_model.get("executor_code"),
        "result_table": selected_model.get("result_table"),
        "confirmed_params": extracted_params,
        "subject_role": subject_role,
        "execution_params": execution_params,
    }


def _build_execution_params(extracted_params: dict[str, Any], subject_role: str) -> dict[str, Any]:
    execution_params = {
        key: value
        for key, value in extracted_params.items()
        if value not in ("", None) and key != "zbdw"
    }
    subject_name = extracted_params.get("zbdw")
    if subject_name not in ("", None):
        if subject_role == "招标人":
            execution_params["name"] = subject_name
        else:
            execution_params["zbdw"] = subject_name
    return execution_params


def _build_execution_confirmation_message(execution_brief: dict[str, Any]) -> str:
    lines = [
        "当前模型和参数已经准备完成，请先向用户展示任务编排并等待明确确认：",
        f"- 已确认模型：`{execution_brief.get('model_name')}`",
    ]
    for key, value in dict(execution_brief.get("confirmed_params") or {}).items():
        lines.append(f"- 已确认参数：`{key}={value}`")
    for key, value in dict(execution_brief.get("execution_params") or {}).items():
        lines.append(f"- 执行参数：`{key}={value}`")
    lines.append("只有用户明确回复“同意”“确认执行”“开始分析”等确认语后，才能进入执行。")
    return "\n".join(lines)


def _build_subject_role_confirmation_message(
    subject_name: str,
    *,
    missing_required_params: list[dict[str, str]] | None = None,
) -> str:
    display_name = subject_name or "当前主体"
    lines = [
        f"主体 `{display_name}` 已完成校验。",
        "请用户再确认一次当前主体口径：",
        "- `招标人`",
        "- `招标单位`",
    ]
    missing = missing_required_params or []
    if missing:
        lines.append("如果以下必填参数也已明确，可以在同一轮一起补充：")
        for item in missing:
            if item.get("param_name") == "zbdw":
                continue
            lines.append(f"- `{item.get('param_name')}`：{item.get('reason')}")
    lines.append("确认后请把主体口径写入 `confirmed_params.subject_role`；如果同轮补充了其他参数，也一并写入 `confirmed_params`。")
    return "\n".join(lines)


def _safe_sql_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
        raise ValueError(f"Invalid SQL identifier: {value}")
    return normalized


def _quote_sql_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _load_model_catalog_row(
    connection_factory: Callable[[], Any],
    model_name: str = "",
    model_id: str = "",
) -> dict[str, Any] | None:
    if model_id:
        sql = f"""
SELECT
    id,
    model_id,
    model_name,
    risk_tag,
    model_type,
    result_table,
    executor_code
FROM public.{MODEL_CATALOG_TABLE}
WHERE model_id = %s
LIMIT 1
""".strip()
        row = _fetch_one_row(connection_factory, sql, [model_id])
        if row is not None:
            return _map_model_catalog_row(row)
    if not model_name:
        return None
    sql = f"""
SELECT
    id,
    model_id,
    model_name,
    risk_tag,
    model_type,
    result_table,
    executor_code
FROM public.{MODEL_CATALOG_TABLE}
WHERE model_name = %s
LIMIT 1
""".strip()
    row = _fetch_one_row(connection_factory, sql, [model_name])
    if row is None:
        return None
    return _map_model_catalog_row(row)


def _load_model_sql_config(
    connection_factory: Callable[[], Any],
    *,
    model_id: str,
    sql_code: str = "main",
) -> dict[str, Any] | None:
    if not model_id:
        return None
    sql = f"""
SELECT
    model_id,
    sql_code,
    sql_text,
    sql_schema,
    description,
    enabled
FROM public.{MODEL_SQL_TABLE}
WHERE model_id = %s AND sql_code = %s AND enabled = TRUE
LIMIT 1
""".strip()
    return _fetch_one_row(connection_factory, sql, [model_id, sql_code])


def _coerce_sql_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"columns": []}
        return parsed if isinstance(parsed, dict) else {"columns": []}
    return {"columns": []}


def _schema_column_names(schema: dict[str, Any]) -> set[str]:
    columns = schema.get("columns")
    if not isinstance(columns, list):
        return set()
    names: set[str] = set()
    for item in columns:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.add(item["name"])
    return names


def _filterable_column_names(schema: dict[str, Any]) -> set[str]:
    explicit = schema.get("filterable_columns")
    if isinstance(explicit, list) and explicit:
        return {str(item) for item in explicit if str(item).strip()}
    return _schema_column_names(schema)


def _exact_filter_column_names(schema: dict[str, Any]) -> set[str]:
    explicit = schema.get("exact_filter_columns")
    if isinstance(explicit, list) and explicit:
        return {str(item) for item in explicit if str(item).strip()}
    return set()


def _build_query_handle(
    model_row: dict[str, Any],
    sql_config: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "model_id": model_row.get("model_id"),
        "model_name": model_row.get("model_name"),
        "sql_code": sql_config.get("sql_code") or "main",
        "params": dict(params or {}),
    }


def _query_handle_filters(query_handle: dict[str, Any]) -> dict[str, Any]:
    params = _coerce_mapping(query_handle.get("params"))
    nested_filters = _coerce_mapping(params.get("filters"))
    if nested_filters:
        return nested_filters
    return params


def _normalize_model_sql_filters(arguments: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    filterable_columns = _filterable_column_names(schema)
    aliases = {
        "zbnd": "招标年度",
        "zbdw": "招标单位",
        "name": "招标人",
        "xmmc": "项目名称",
        "bdmc": "标段名称",
        "tbrmc1": "投标人1",
        "tbrmc2": "投标人2",
        "ydlx": "疑点类型",
        "risk_level": "风险等级",
    }
    filters: dict[str, Any] = {}
    nested_filters = _coerce_mapping(arguments.get("filters"))
    for source in (arguments, nested_filters):
        for key, value in source.items():
            target = aliases.get(str(key), str(key))
            if target in filterable_columns and value not in (None, "", []):
                filters[target] = value
    return filters


def _merge_dataset_filters(
    query_handle: dict[str, Any],
    request_filters: dict[str, Any],
) -> dict[str, Any]:
    merged = _query_handle_filters(query_handle)
    merged.update(request_filters)
    return {key: value for key, value in merged.items() if value not in (None, "", [])}


def _query_configured_sql_page(
    *,
    connection_factory: Callable[[], Any],
    sql_text: str,
    schema: dict[str, Any],
    filters: dict[str, Any],
    order_by: dict[str, Any],
    page: int,
    page_size: int,
    model_id: str = "",
) -> list[dict[str, Any]]:
    query = _build_configured_sql_query(
        sql_text=sql_text,
        schema=schema,
        model_id=model_id,
        filters=filters,
    )
    sql = query["sql"]
    if not sql:
        return []
    params = list(query["params"])
    selectable_schema = _display_schema_for_model(model_id=model_id, schema=schema, filters=filters)
    selectable_columns = _schema_column_names(selectable_schema)
    order_column = str(order_by.get("column") or schema.get("default_order_by_column") or "").strip()
    direction = str(order_by.get("direction") or "DESC").strip().upper()
    if direction not in {"ASC", "DESC"}:
        direction = "DESC"
    if order_column and (not selectable_columns or order_column in selectable_columns):
        sql += f" ORDER BY {_quote_sql_identifier(order_column)} {direction}"
    elif isinstance(schema.get("default_order_by"), str) and schema["default_order_by"].strip():
        default_order = str(schema["default_order_by"]).strip()
        if re.fullmatch(r'[\w\u4e00-\u9fa5、（）()]+(?:\s+(?:ASC|DESC))?', default_order, re.IGNORECASE):
            parts = default_order.rsplit(" ", 1)
            default_column = parts[0] if len(parts) == 2 and parts[1].upper() in {"ASC", "DESC"} else default_order
            default_direction = parts[1].upper() if len(parts) == 2 and parts[1].upper() in {"ASC", "DESC"} else "DESC"
            if not selectable_columns or default_column in selectable_columns:
                sql += f" ORDER BY {_quote_sql_identifier(default_column)} {default_direction}"
    sql += " LIMIT %s OFFSET %s"
    params.extend([page_size + 1, (page - 1) * page_size])
    return [
        _normalize_model_result_row(row)
        for row in _fetch_rows(connection_factory, sql, params)
    ]


def _build_configured_sql_query(
    *,
    sql_text: str,
    schema: dict[str, Any],
    filters: dict[str, Any],
    model_id: str = "",
) -> dict[str, Any]:
    base_sql = _strip_sql_semicolon(sql_text).replace("%", "%%")
    if not base_sql:
        return {"sql": "", "params": []}
    filterable_columns = _filterable_column_names(schema)
    exact_filter_columns = _exact_filter_column_names(schema)
    where_clauses: list[str] = []
    params: list[Any] = []
    for column, value in filters.items():
        if value in ("", None, []):
            continue
        column_name = str(column)
        if column_name not in filterable_columns:
            continue
        if column_name in exact_filter_columns:
            where_clauses.append(f"CAST({_quote_sql_identifier(column_name)} AS TEXT) = %s")
            params.append(str(value))
        else:
            where_clauses.append(f"CAST({_quote_sql_identifier(column_name)} AS TEXT) ILIKE %s")
            params.append(f"%{value}%")
    sql = f"SELECT * FROM ({base_sql}) AS model_result"
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    if _should_aggregate_bid_collusion_scene(model_id, schema):
        sql = _build_bid_collusion_scene_aggregate_sql(sql, filters)
    return {"sql": sql, "params": params}


def _should_aggregate_bid_collusion_scene(model_id: str, schema: dict[str, Any]) -> bool:
    if model_id != BID_COLLUSION_SCENE_MODEL_ID:
        return False
    names = set(_schema_column_names(schema))
    required = {
        "招标人",
        "招标年度",
        "招标单位",
        "投标人1",
        "投标人2",
        "共同参与次数",
        "a中标次数",
        "b中标次数",
    }
    return required.issubset(names)


def _scene_context_group_columns(filters: dict[str, Any]) -> list[str]:
    # 场景结果的展示粒度由用户已经确认的上下文维度决定，不能把未确认的招标人强行带入分组。
    ordered = ["招标人", "招标年度", "招标单位"]
    return [column for column in ordered if filters.get(column) not in (None, "", [])]


def _build_bid_collusion_scene_aggregate_sql(filtered_sql: str, filters: dict[str, Any]) -> str:
    group_columns = _scene_context_group_columns(filters) + ["投标人1", "投标人2"]
    group_select = ", ".join(_quote_sql_identifier(column) for column in group_columns)
    group_by = ", ".join(_quote_sql_identifier(column) for column in group_columns)
    risk_sums = [
        f"SUM({_quote_sql_identifier(column)}) AS {_quote_sql_identifier(column)}"
        for column in scene_dimension_fields()
    ]
    select_parts = [
        group_select,
        *risk_sums,
        (
            "CASE "
            f"WHEN SUM({_quote_sql_identifier('共同参与次数')}) >= 10 THEN '高' "
            f"WHEN SUM({_quote_sql_identifier('共同参与次数')}) >= 5 THEN '中' "
            "ELSE '低' END AS \"风险等级\""
        ),
        f"SUM({_quote_sql_identifier('共同参与次数')}) AS {_quote_sql_identifier('共同参与次数')}",
        f"SUM({_quote_sql_identifier('a中标次数')}) AS {_quote_sql_identifier('a中标次数')}",
        f"SUM({_quote_sql_identifier('b中标次数')}) AS {_quote_sql_identifier('b中标次数')}",
        (
            f"SUM({_quote_sql_identifier('共同参与次数')}) "
            f"- SUM({_quote_sql_identifier('a中标次数')}) "
            f"- SUM({_quote_sql_identifier('b中标次数')}) AS {_quote_sql_identifier('均未中标次数')}"
        ),
        (
            f"ROUND((SUM({_quote_sql_identifier('a中标次数')}) + SUM({_quote_sql_identifier('b中标次数')})) "
            f"/ NULLIF(SUM({_quote_sql_identifier('共同参与次数')}), 0) * 100, 2) || '%%' "
            f"AS {_quote_sql_identifier('中标集中度')}"
        ),
    ]
    return (
        "SELECT "
        + ", ".join(select_parts)
        + f" FROM ({filtered_sql}) AS scene_filtered GROUP BY {group_by}"
    )


def _display_schema_for_model(
    *,
    model_id: str,
    schema: dict[str, Any],
    filters: dict[str, Any],
) -> dict[str, Any]:
    if not _should_aggregate_bid_collusion_scene(model_id, schema):
        return schema
    included_context = set(_scene_context_group_columns(filters))
    hidden_context = {"招标人", "招标年度", "招标单位"} - included_context
    if not hidden_context:
        return schema
    adjusted = dict(schema)
    columns = schema.get("columns")
    if isinstance(columns, list):
        adjusted["columns"] = [
            column
            for column in columns
            if not isinstance(column, dict) or str(column.get("name") or "") not in hidden_context
        ]
    return adjusted


def _normalize_model_result_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        normalized_key = {
            TRUNCATED_PERSON_REPEAT_FIELD: PERSON_REPEAT_FIELD,
            FULL_PERSON_REPEAT_FIELD: PERSON_REPEAT_FIELD,
        }.get(key, key)
        normalized[normalized_key] = value
    return normalized


def _persist_query_result_batch(
    *,
    connection_factory: Callable[[], Any],
    result_table: str,
    sql: str,
    params: list[Any],
    execution_id: str,
    session_id: str | None,
    task_id: str | None,
    column_name_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    conn = connection_factory()
    try:
        cur = conn.cursor()
        try:
            if params:
                _log_sql_execution("_persist_query_result_batch.source", sql, params)
                cur.execute(sql, params)
            else:
                _log_sql_execution("_persist_query_result_batch.source", sql, [])
                cur.execute(sql)
            source_columns = [item[0] for item in cur.description]
            result_columns = [
                (column_name_mapping or {}).get(column_name, column_name)
                for column_name in source_columns
            ]
            records = cur.fetchall()
            written_row_count = len(records)
            if records:
                metadata_columns = ["execution_id", "session_id", "task_id", "created_at"]
                all_columns = metadata_columns + result_columns
                insert_sql = (
                    f'INSERT INTO public."{result_table}" '
                    f"({','.join(_quote_sql_identifier(col) for col in all_columns)}) "
                    f"VALUES ({','.join(['%s'] * len(all_columns))})"
                )
                insert_params = [
                    [execution_id, session_id, task_id, datetime.now(), *row]
                    for row in records
                ]
                _log_sql_execution(
                    "_persist_query_result_batch.insert_many",
                    insert_sql,
                    {
                        "batch_size": len(insert_params),
                        "first_params": insert_params[0] if insert_params else None,
                    },
                )
                cur.executemany(insert_sql, insert_params)
            preview_sql = f'SELECT * FROM public."{result_table}" WHERE execution_id = %s LIMIT %s'
            _log_sql_execution("_persist_query_result_batch.preview", preview_sql, [execution_id, 5])
            cur.execute(preview_sql, [execution_id, 5])
            preview_rows = _as_rows([item[0] for item in cur.description], cur.fetchall())
        finally:
            cur.close()
        if hasattr(conn, "commit"):
            conn.commit()
    finally:
        conn.close()
    return {
        "written_row_count": written_row_count,
        "result_table": result_table,
        "execution_id": execution_id,
        "preview_rows": preview_rows,
    }


NATIONAL_PIPELINE_BID_COLLUSION_SCENE_SQL = r"""WITH
check_table1 AS (
SELECT
    t1.name,
    t1.zbnd,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    substr(CAST(t1.cqsj AS VARCHAR), 0, 11) AS cqsj_date,
    t1.tbrmc AS tbrmc1,
    t2.tbrmc AS tbrmc2,
    CASE WHEN t1.sfzb = '是' THEN 1 ELSE 0 END AS sfzb1_num,
    CASE WHEN t2.sfzb = '是' THEN 1 ELSE 0 END AS sfzb2_num,
    '人员重复' AS ydlx
FROM
    rst_sj_dzzb_tblxrxt t1
JOIN
    rst_sj_dzzb_tblxrxt t2
ON
    t1.name = t2.name
    AND t1.zbnd = t2.zbnd
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.bdmc = t2.bdmc
    AND substr(CAST(t1.cqsj AS VARCHAR), 0, 11) = substr(CAST(t2.cqsj AS VARCHAR), 0, 11)
    AND t1.tbrmc <> t2.tbrmc
    AND t1.tbrmc < t2.tbrmc
GROUP BY
    t1.name, t1.zbnd, t1.zbdw, t1.xmmc, t1.bdmc, cqsj_date, t1.tbrmc, t2.tbrmc,t1.sfzb,t2.sfzb
ORDER BY
    name, zbnd, xmmc, bdmc, cqsj_date, tbrmc1, tbrmc2
),
check_table2 AS (
SELECT
    t1.name,
    t1.zbdw,
    t1.xmmc,
    t1.bdmc,
    t1.tbsqrdb AS 代表人1,
    t2.tbsqrdb AS 代表人2,
    t1.tbrmc AS 投标人1,
    t2.tbrmc AS 投标人2,
    substr(cast(t1.cqsj as varchar) ,0,11) as cqsj1,
    '电话邮箱相同' AS ydlx
FROM
    rst_sj_dzzb_tbrdhyxxt t1
JOIN
    rst_sj_dzzb_tbrdhyxxt t2
ON
    t1.name = t2.name
    AND t1.zbdw = t2.zbdw
    AND t1.xmmc = t2.xmmc
    AND t1.bdmc = t2.bdmc
    AND t1.tbsqrdbdh = t2.tbsqrdbdh
    AND t1.tbsqrdbyx = t2.tbsqrdbyx
    AND t1.tbsqrdb <> t2.tbsqrdb
    AND t1.tbsqrdb < t2.tbsqrdb
GROUP BY
    t1.name, t1.zbdw, t1.xmmc, t1.bdmc,
    t1.tbsqrdbdh, t1.tbsqrdbyx, t1.tbsqrdb, t2.tbsqrdb,
    t1.tbrmc, t2.tbrmc,cqsj1
ORDER BY
    name, zbdw, xmmc, bdmc),
check_table3 AS (
    SELECT
        t1.name,
        '' AS zbnd,
        t1.zbdw,
        t1.xmmc,
        t1.bdmc,
        TO_CHAR(t1.cqsj, 'YYYY-MM-DD') AS cqsj_date,
        t1.tbrmc AS tbrmc1,
        t2.tbrmc AS tbrmc2,
        0 AS sfzb1_num,
        0 AS sfzb2_num,
        '硬件或IP重复' AS ydlx
    FROM
        rst_sj_dzzb_yjjipyz t1
    JOIN
        rst_sj_dzzb_yjjipyz t2
    ON
        t1.name = t2.name
        AND t1.zbdw = t2.zbdw
        AND t1.xmmc = t2.xmmc
        AND t1.bdmc = t2.bdmc
        AND TO_CHAR(t1.cqsj, 'YYYY-MM-DD') = TO_CHAR(t2.cqsj, 'YYYY-MM-DD')
        AND t1.tbrmc <> t2.tbrmc
        AND t1.tbrmc < t2.tbrmc
    GROUP BY
        t1.name, zbnd, t1.zbdw, t1.xmmc, t1.bdmc, cqsj_date, t1.tbrmc, t2.tbrmc
),
check_table4 AS (
    SELECT
        t1.name,
        '' AS zbnd,
        t1.zbdw,
        t1.xmmc,
        t1.bdmc,
        TO_CHAR(t1.cqsj, 'YYYY-MM-DD') AS cqsj_date,
        t1.tbrmc AS tbrmc1,
        t2.tbrmc AS tbrmc2,
        0 AS sfzb1_num,
        0 AS sfzb2_num,
        '保证金同一账户转出' AS ydlx
    FROM
        rst_sj_dzzb_tbbzjctyzhzc t1
    JOIN
        rst_sj_dzzb_tbbzjctyzhzc t2
    ON
        t1.name = t2.name
        AND t1.zbdw = t2.zbdw
        AND t1.xmmc = t2.xmmc
        AND t1.bdmc = t2.bdmc
        AND t1.hkyhzh = t2.hkyhzh
        AND TO_CHAR(t1.cqsj, 'YYYY-MM-DD') = TO_CHAR(t2.cqsj, 'YYYY-MM-DD')
        AND t1.tbrmc <> t2.tbrmc
        AND t1.tbrmc < t2.tbrmc
    GROUP BY
        t1.name, zbnd, t1.zbdw, t1.xmmc, t1.bdmc, cqsj_date, t1.tbrmc, t2.tbrmc
),
check_table5 AS (
    SELECT
        t1.name,
        '' AS zbnd,
        t1.zbdw,
        t1.xmmc,
        '' AS bdmc,
        TO_CHAR(t1.cqsj, 'YYYY-MM-DD') AS cqsj_date,
        t1.tbrmc AS tbrmc1,
        t2.tbrmc AS tbrmc2,
        0 AS sfzb1_num,
        0 AS sfzb2_num,
        '投标人完全一致' AS ydlx
    FROM
        rst_sj_dzzb_tbrwqyz t1
    JOIN
        rst_sj_dzzb_tbrwqyz t2
    ON
        t1.name = t2.name
        AND t1.zbdw = t2.zbdw
        AND t1.xmmc = t2.xmmc
        AND t1.tbrmc <> t2.tbrmc
        AND t1.tbrmc < t2.tbrmc
        AND TO_CHAR(t1.cqsj, 'YYYY-MM-DD') = TO_CHAR(t2.cqsj, 'YYYY-MM-DD')
    GROUP BY
        t1.name, zbnd, t1.zbdw, t1.xmmc, bdmc, cqsj_date, t1.tbrmc, t2.tbrmc
),
all_data as(
SELECT t1.name,
        t1.zbnd,
        t1.zbdw,
        t1.xmmc,
        t1.bdmc,
        t1.cqsj_date,
        t1.tbrmc1,
        t1.tbrmc2,
        t1.sfzb1_num,
        t1.sfzb2_num,
        t1.ydlx
FROM check_table1 t1
UNION ALL
SELECT t2.name,
        '2025' as zbnd,
        t2.zbdw,
        t2.xmmc,
        t2.bdmc,
        t2.cqsj1 AS cqsj_date,
        t2.投标人1 AS tbrmc1,
        t2.投标人2 AS tbrmc2,
        0 as sfzb1_num,
        0 as sfzb2_num,
        t2.ydlx
FROM check_table2 t2
union all
SELECT
    t3.name,
    t3.zbnd,
    t3.zbdw,
    t3.xmmc,
    t3.bdmc,
    t3.cqsj_date,
    t3.tbrmc1,
    t3.tbrmc2,
    t3.sfzb1_num,
    t3.sfzb2_num,
    t3.ydlx
FROM check_table3 t3
union all
SELECT
    t4.name,
    t4.zbnd,
    t4.zbdw,
    t4.xmmc,
    t4.bdmc,
    t4.cqsj_date,
    t4.tbrmc1,
    t4.tbrmc2,
    t4.sfzb1_num,
    t4.sfzb2_num,
    t4.ydlx
FROM check_table4 t4
union all
SELECT
    t5.name,
    t5.zbnd,
    t5.zbdw,
    t5.xmmc,
    t5.bdmc,
    t5.cqsj_date,
    t5.tbrmc1,
    t5.tbrmc2,
    t5.sfzb1_num,
    t5.sfzb2_num,
    t5.ydlx
FROM check_table5 t5
),
result AS (
    select t.*,
           t.共同参与次数::NUMERIC - t.A中标次数::NUMERIC - t.B中标次数::NUMERIC as 均未中标次数,
           ROUND( (t.A中标次数::NUMERIC + t.B中标次数::NUMERIC) / t.共同参与次数::NUMERIC * 100, 2) AS 中标集中度
    from (
    SELECT
        tbrmc1,
        tbrmc2,
        COUNT(*) AS 共同参与次数,
        SUM(CASE WHEN ydlx = '电话邮箱相同' THEN 1 ELSE 0 END) AS 不同投标人电话邮箱相同,
        SUM(CASE WHEN ydlx = '投标人完全一致' THEN 1 ELSE 0 END) AS 不同标段投标人完全一致,
        SUM(CASE WHEN ydlx = '硬件或IP重复' THEN 1 ELSE 0 END) AS 不同投标人硬件及ip地址一致,
        SUM(CASE WHEN ydlx = '保证金同一账户转出' THEN 1 ELSE 0 END)  AS 投标保证金从同一账户转出,
        SUM(CASE WHEN ydlx = '人员重复' THEN 1 ELSE 0 END)  AS 不同投标人的投标联系人、授权代表人、项目负责人相同,
        SUM(sfzb1_num) AS A中标次数,
        SUM(sfzb2_num) AS B中标次数
    FROM all_data
    GROUP BY tbrmc1, tbrmc2)t
)
SELECT
    tbrmc1             AS 投标人1,
    tbrmc2             AS 投标人2,
    不同投标人电话邮箱相同,
    不同标段投标人完全一致,
    不同投标人硬件及ip地址一致,
    投标保证金从同一账户转出,
    不同投标人的投标联系人、授权代表人、项目负责人相同、有关系的投标人组,
    CASE
        WHEN 共同参与次数 >= 10 THEN '高'
        WHEN 共同参与次数 >= 5  THEN '中'
        ELSE '低'
    END                AS 风险等级,
    共同参与次数,
    A中标次数,
    B中标次数,
    均未中标次数,
    中标集中度 || '%'  AS 中标集中度
FROM result
ORDER BY 共同参与次数 DESC;"""


def _execute_bid_collusion_scene_model(
    *,
    connection_factory: Callable[[], Any],
    model_row: dict[str, Any],
    arguments: dict[str, Any],
    execution_id: str,
    session_id: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    result_table = _safe_sql_identifier(str(model_row.get("result_table") or ""))
    sql, params = build_bid_collusion_query(arguments)
    return _persist_query_result_batch(
        connection_factory=connection_factory,
        result_table=result_table,
        sql=sql,
        params=params,
        execution_id=execution_id,
        session_id=session_id,
        task_id=task_id,
    )


def _execute_bid_collusion_detail_model(
    *,
    connection_factory: Callable[[], Any],
    model_row: dict[str, Any],
    arguments: dict[str, Any],
    execution_id: str,
    session_id: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    result_table = _safe_sql_identifier(str(model_row.get("result_table") or ""))
    sql, params = build_bid_collusion_detail_query(arguments)
    return _persist_query_result_batch(
        connection_factory=connection_factory,
        result_table=result_table,
        sql=sql,
        params=params,
        execution_id=execution_id,
        session_id=session_id,
        task_id=task_id,
    )


def _execute_sql_preview_model(
    *,
    connection_factory: Callable[[], Any],
    model_row: dict[str, Any],
    arguments: dict[str, Any],
    execution_id: str,
    session_id: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    model_id = str(model_row.get("model_id") or model_row.get("id") or "").strip()
    sql_config = _load_model_sql_config(
        connection_factory,
        model_id=model_id,
        sql_code=str(arguments.get("sql_code") or "main").strip() or "main",
    )
    if sql_config is None:
        return {
            "ok": False,
            "type": MODEL_RESULT_DATASET_TYPE,
            "artifact_type": MODEL_RESULT_DATASET_TYPE,
            "model_id": model_id,
            "summary": f"模型“{model_row.get('model_name')}”未配置可执行 SQL。",
        }
    schema = _coerce_sql_schema(sql_config.get("sql_schema"))
    filters = _normalize_model_sql_filters(arguments, schema)
    preview_rows = _query_configured_sql_page(
        connection_factory=connection_factory,
        sql_text=str(sql_config.get("sql_text") or ""),
        schema=schema,
        model_id=model_id,
        filters=filters,
        order_by={},
        page=1,
        page_size=5,
    )[:5]
    query_handle = _build_query_handle(model_row, sql_config, {"filters": filters})
    response_schema = _display_schema_for_model(model_id=model_id, schema=schema, filters=filters)
    return {
        "type": MODEL_RESULT_DATASET_TYPE,
        "artifact_type": MODEL_RESULT_DATASET_TYPE,
        "tool_type": "model_result_dataset",
        "next_action": "query_result_table",
        "next_tool": "query_model_result_table",
        "strict_user_message": "模型执行只创建了可分页结果集预览。preview_rows 仅用于界面预览，不能据此输出最终分析；下一步必须使用 query_handle 调用 query_model_result_table 读取当前页数据。",
        "model_id": model_row.get("model_id"),
        "sql_code": sql_config.get("sql_code"),
        "schema": response_schema,
        "query_handle": query_handle,
        "preview_rows": preview_rows,
        "preview_count": len(preview_rows),
        "summary": f"模型“{model_row.get('model_name')}”已生成可分页结果集预览。",
    }


def _execute_national_pipeline_bid_collusion_scene_model(
    *,
    connection_factory: Callable[[], Any],
    model_row: dict[str, Any],
    arguments: dict[str, Any],
    execution_id: str,
    session_id: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    result_table = _safe_sql_identifier(str(model_row.get("result_table") or ""))
    return _persist_query_result_batch(
        connection_factory=connection_factory,
        result_table=result_table,
        sql=NATIONAL_PIPELINE_BID_COLLUSION_SCENE_SQL,
        params=[],
        execution_id=execution_id,
        session_id=session_id,
        task_id=task_id,
        column_name_mapping={
            TRUNCATED_PERSON_REPEAT_FIELD: PERSON_REPEAT_FIELD,
            FULL_PERSON_REPEAT_FIELD: PERSON_REPEAT_FIELD,
        },
    )


def _is_select_all_query(value: str) -> bool:
    return value.strip().lower() in {"*", "select *", "select all", "all", "全部", "全量"}


def _coerce_limit(value: Any, *, default: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, 500))


def _normalize_indicator_phrase(value: str) -> str:
    normalized = value.strip(" ，。；：:,.")
    normalized = re.sub(
        r"^((请帮我|帮我|请|查询|查看|看一下|看下|获取|找一下|找下|指标查询))+",
        "",
        normalized,
    )
    normalized = re.sub(r"(是多少|是什么|的值|数值|数据)$", "", normalized)
    return normalized.strip(" ，。；：:,.")


def _normalize_model_phrase(value: str) -> str:
    normalized = value.strip(" ，。；：:,.")
    normalized = re.sub(
        r"^((请帮我|帮我|请|查询|查看|看一下|看下|获取|找一下|找下|模型查询|模型清单))+",
        "",
        normalized,
    )
    normalized = re.sub(r"(模型|清单|配置|信息|是什么|有哪些)$", "", normalized)
    return normalized.strip(" ，。；：:,.") or value.strip()


def _coerce_model_type_filter(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[\s，。；：:,.、/]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item) for item in value]
    else:
        raw_items = [str(value)]
    items = [item.strip() for item in raw_items if item and item.strip()]
    return list(dict.fromkeys(items))


def _model_type_filter_clause(model_types: list[str]) -> tuple[str, list[str]]:
    if not model_types:
        return "", []
    placeholders = ", ".join(["%s"] * len(model_types))
    return f"AND model_type IN ({placeholders})", model_types


def _indicator_search_terms(value: str) -> list[str]:
    normalized = _normalize_indicator_phrase(value)
    terms: list[str] = []
    for year in re.findall(r"\d{4}", normalized):
        terms.append(year)
    semantic_text = re.sub(r"\d{4}年?", "", normalized)
    semantic_text = re.sub(r"(指标|数据|数值|情况|的|了|一下|帮我|请|查询|查看)", "", semantic_text)
    for term in re.split(r"[\s，。；：:,.、（）()]+", semantic_text):
        term = term.strip()
        if term:
            terms.append(term)
    if not terms and normalized:
        terms.append(normalized)
    return list(dict.fromkeys(terms)) or [value]


def _model_search_terms(value: str) -> list[str]:
    normalized = _normalize_model_phrase(value)
    semantic_text = re.sub(r"(模型|清单|配置|信息|是什么|有哪些|查询|查看)", "", normalized)
    terms = [term.strip() for term in re.split(r"[\s，。；：:,.、（）()/]+", semantic_text) if term.strip()]
    if not terms and normalized:
        terms.append(normalized)
    return list(dict.fromkeys(terms)) or [value]


def _indicator_candidate_score(query_text: str, candidate_name: str) -> tuple[int, int]:
    terms = _indicator_search_terms(query_text)
    hit_count = sum(1 for term in terms if term and term in candidate_name)
    year_bonus = 1 if re.search(r"\d{4}", query_text) and re.search(r"\d{4}", query_text).group(0) in candidate_name else 0
    return (hit_count + year_bonus, -len(candidate_name))

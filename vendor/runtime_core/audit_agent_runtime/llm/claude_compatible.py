from __future__ import annotations

import json
import logging

import httpx

from audit_agent_runtime.llm.base import (
    ModelMessage,
    ModelResponse,
    ModelStreamEvent,
    ToolCall,
)
from audit_agent_runtime.llm.payload_diagnostics import (
    compact_provider_payload_diagnostics,
    dump_log_payload,
    log_model_request_payload,
)
from audit_agent_runtime.llm.request_assembly import (
    build_claude_messages_payload,
    normalize_messages_for_api_with_diagnostics,
)


logger = logging.getLogger(__name__)


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _parse_tool_input(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        logger.warning("failed to parse anthropic tool input json: %s", value)
        return {}
    return parsed if isinstance(parsed, dict) else {}


class ClaudeCompatibleModelClient:
    TASK_BUDGETS_BETA_HEADER = "task-budgets-2026-03-13"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        enable_thinking: bool = False,
        thinking_budget: int | None = None,
        timeout_seconds: float = 300.0,
        http_client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.enable_thinking = enable_thinking
        self.thinking_budget = thinking_budget
        self.http_client = http_client or httpx.Client(timeout=timeout_seconds)
        self.last_provider_payload_diagnostics: dict | None = None

    def _messages_url(self) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/messages?beta=true"
        return f"{self.base_url}/v1/messages?beta=true"

    def _headers(self, *, task_budget: object = None) -> dict[str, str]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if task_budget is not None:
            headers["anthropic-beta"] = self.TASK_BUDGETS_BETA_HEADER
        return headers

    def complete(
        self,
        messages: list[ModelMessage],
        tools: list[dict],
        metadata: dict | None = None,
        task_budget: int | None = None,
    ) -> ModelResponse:
        self._record_provider_payload_diagnostics(messages)
        request_payload = build_claude_messages_payload(
            model_name=self.model_name,
            messages=messages,
            tools=tools,
            metadata=metadata,
            task_budget=task_budget,
            enable_thinking=self.enable_thinking,
            thinking_budget=self.thinking_budget,
        )
        log_model_request_payload(logger, label="llm request", payload=request_payload)
        response = self.http_client.post(
            self._messages_url(),
            headers=self._headers(task_budget=task_budget),
            json=request_payload,
        )
        response.raise_for_status()
        payload = _as_dict(response.json())
        logger.info(
            "llm response payload: status=%s body=%s",
            response.status_code,
            dump_log_payload(payload),
        )
        output_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in _as_list(payload.get("content")):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                output_parts.append(block["text"])
                continue
            if block.get("type") != "tool_use":
                continue
            tool_id = block.get("id")
            tool_name = block.get("name")
            if not isinstance(tool_id, str) or not isinstance(tool_name, str):
                continue
            tool_calls.append(
                ToolCall(
                    call_id=tool_id,
                    tool_name=tool_name,
                    arguments=_parse_tool_input(block.get("input")),
                )
            )
        return ModelResponse(
            output_text="".join(output_parts),
            tool_calls=tool_calls,
            stop_reason=payload.get("stop_reason"),
        )

    def stream(
        self,
        messages: list[ModelMessage],
        tools: list[dict],
        metadata: dict | None = None,
        task_budget: int | None = None,
    ):
        self._record_provider_payload_diagnostics(messages)
        request_payload = build_claude_messages_payload(
            model_name=self.model_name,
            messages=messages,
            tools=tools,
            metadata=metadata,
            task_budget=task_budget,
            enable_thinking=self.enable_thinking,
            thinking_budget=self.thinking_budget,
            stream=True,
        )
        log_model_request_payload(logger, label="llm stream request", payload=request_payload)
        with self.http_client.stream(
            "POST",
            self._messages_url(),
            headers=self._headers(task_budget=task_budget),
            json=request_payload,
        ) as response:
            response.raise_for_status()
            logger.info("llm stream response opened: status=%s", response.status_code)
            current_event = ""
            stop_reason: str | None = None
            tool_calls: dict[int, dict] = {}
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    current_event = line[6:].lstrip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].lstrip()
                logger.debug("llm stream event: %s %s", current_event, data)
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("failed to parse anthropic stream event json: %s", data)
                    continue
                if current_event == "content_block_start":
                    index = payload.get("index", 0)
                    block = _as_dict(payload.get("content_block"))
                    if block.get("type") == "tool_use":
                        tool_calls[index] = {
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "json_chunks": [],
                            "input": block.get("input", {}),
                        }
                elif current_event == "content_block_delta":
                    index = payload.get("index", 0)
                    delta = _as_dict(payload.get("delta"))
                    delta_type = delta.get("type")
                    if delta_type == "text_delta" and isinstance(delta.get("text"), str):
                        yield ModelStreamEvent(type="text_delta", text=delta["text"])
                    elif delta_type == "thinking_delta" and isinstance(delta.get("thinking"), str):
                        yield ModelStreamEvent(type="reasoning_delta", text=delta["thinking"])
                    elif delta_type == "input_json_delta":
                        current = tool_calls.setdefault(
                            index,
                            {"id": None, "name": None, "json_chunks": [], "input": {}},
                        )
                        partial_json = delta.get("partial_json")
                        if isinstance(partial_json, str):
                            current["json_chunks"].append(partial_json)
                elif current_event == "content_block_stop":
                    index = payload.get("index", 0)
                    current = tool_calls.get(index)
                    if not isinstance(current, dict):
                        continue
                    tool_id = current.get("id")
                    tool_name = current.get("name")
                    if not isinstance(tool_id, str) or not isinstance(tool_name, str):
                        continue
                    if current.get("json_chunks"):
                        tool_input = _parse_tool_input("".join(current["json_chunks"]))
                    else:
                        tool_input = _parse_tool_input(current.get("input"))
                    yield ModelStreamEvent(
                        type="tool_call",
                        tool_call=ToolCall(
                            call_id=tool_id,
                            tool_name=tool_name,
                            arguments=tool_input,
                        ),
                    )
                elif current_event == "message_delta":
                    delta = _as_dict(payload.get("delta"))
                    if isinstance(delta.get("stop_reason"), str):
                        stop_reason = delta["stop_reason"]
                elif current_event == "message_stop":
                    break

            yield ModelStreamEvent(type="done", stop_reason=stop_reason)

    def _record_provider_payload_diagnostics(self, messages: list[ModelMessage]) -> None:
        self.last_provider_payload_diagnostics = compact_provider_payload_diagnostics(
            normalize_messages_for_api_with_diagnostics(messages).diagnostics
        )

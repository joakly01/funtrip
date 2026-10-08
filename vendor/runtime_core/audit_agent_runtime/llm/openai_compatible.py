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
    build_openai_chat_payload,
    normalize_messages_for_api_with_diagnostics,
)
from audit_agent_runtime.llm.thinking_policy import ThinkingPolicy


logger = logging.getLogger(__name__)


def _parse_tool_arguments(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        logger.warning("failed to parse tool arguments json: %s", value)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tool_call_items(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _first_choice(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    choice = choices[0]
    return choice if isinstance(choice, dict) else {}


def _message_container(choice: object) -> dict:
    if not isinstance(choice, dict):
        return {}
    message = choice.get("message")
    return message if isinstance(message, dict) else {}


def _delta_container(choice: object) -> dict:
    if not isinstance(choice, dict):
        return {}
    delta = choice.get("delta")
    return delta if isinstance(delta, dict) else {}


def _sanitize_visible_content(text: object, *, strip_thinking_artifacts: bool) -> str:
    if not isinstance(text, str):
        return ""
    if not strip_thinking_artifacts:
        return text
    if "</think>" in text:
        return text.rsplit("</think>", 1)[-1]
    if "<think>" in text:
        return text.split("<think>", 1)[0]
    return text


class OpenAICompatibleModelClient:
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
        self.thinking_policy = ThinkingPolicy(
            provider="openai",
            model_name=model_name,
            requested_enabled=enable_thinking,
        )
        self.last_provider_payload_diagnostics: dict | None = None

    def complete(self, messages: list[ModelMessage], tools: list[dict]) -> ModelResponse:
        self._record_provider_payload_diagnostics(messages)
        request_payload = build_openai_chat_payload(
            model_name=self.model_name,
            messages=messages,
            tools=tools,
            enable_thinking=self.enable_thinking,
            thinking_budget=self.thinking_budget,
            thinking_policy=self.thinking_policy,
        )
        log_model_request_payload(logger, label="llm request", payload=request_payload)
        response = self.http_client.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
        )
        _raise_for_status_with_body(response)
        payload = response.json()
        logger.info(
            "llm response payload: status=%s body=%s",
            response.status_code,
            dump_log_payload(payload),
        )
        choice = _first_choice(payload)
        message = _message_container(choice)
        reasoning_content = None
        if self.enable_thinking:
            reasoning_content = (
                message.get("reasoning_content")
                or message.get("reasoning")
                or message.get("reasoning_text")
            )

        tool_calls = []
        for tool_call in _tool_call_items(message.get("tool_calls", [])):
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            call_id = tool_call.get("id")
            tool_name = function.get("name")
            if not isinstance(call_id, str) or not isinstance(tool_name, str):
                continue
            tool_calls.append(
                ToolCall(
                    call_id=call_id,
                    tool_name=tool_name,
                    arguments=_parse_tool_arguments(function.get("arguments")),
                )
            )
        return ModelResponse(
            output_text=_sanitize_visible_content(
                message.get("content"),
                strip_thinking_artifacts=not self.enable_thinking,
            ),
            tool_calls=tool_calls,
            stop_reason=choice.get("finish_reason"),
            reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
            usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
        )

    def stream(self, messages: list[ModelMessage], tools: list[dict]):
        self._record_provider_payload_diagnostics(messages)
        request_payload = build_openai_chat_payload(
            model_name=self.model_name,
            messages=messages,
            tools=tools,
            enable_thinking=self.enable_thinking,
            thinking_budget=self.thinking_budget,
            stream=True,
            thinking_policy=self.thinking_policy,
        )
        log_model_request_payload(logger, label="llm stream request", payload=request_payload)
        with self.http_client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
        ) as response:
            _raise_for_status_with_body(response)
            logger.info("llm stream response opened: status=%s", response.status_code)
            tool_calls: dict[int, dict] = {}
            choice: dict = {}
            usage: dict | None = None
            reasoning_parts: list[str] = []
            for line in response.iter_lines():
                if not line:
                    continue
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                logger.debug("llm stream event: %s", data)
                if data == "[DONE]":
                    break

                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("failed to parse stream event json: %s", data)
                    continue
                payload_usage = payload.get("usage") if isinstance(payload, dict) else None
                if isinstance(payload_usage, dict):
                    usage = payload_usage

                next_choice = _first_choice(payload)
                if not next_choice:
                    continue
                choice = next_choice
                delta = _delta_container(choice)

                reasoning_text = None
                if self.enable_thinking:
                    reasoning_text = (
                        delta.get("reasoning_content")
                        or delta.get("reasoning")
                        or delta.get("reasoning_text")
                    )
                if reasoning_text:
                    reasoning_parts.append(str(reasoning_text))
                    yield ModelStreamEvent(type="reasoning_delta", text=reasoning_text)

                content_text = delta.get("content")
                content_text = _sanitize_visible_content(
                    content_text,
                    strip_thinking_artifacts=not self.enable_thinking,
                )
                if content_text:
                    yield ModelStreamEvent(type="text_delta", text=content_text)

                for item in _tool_call_items(delta.get("tool_calls", [])):
                    index = item.get("index", 0)
                    current = tool_calls.setdefault(
                        index,
                        {
                            "id": item.get("id"),
                            "name": "",
                            "arguments": "",
                        },
                    )
                    if item.get("id"):
                        current["id"] = item["id"]
                    function = item.get("function")
                    if not isinstance(function, dict):
                        continue
                    if function.get("name"):
                        current["name"] = function["name"]
                    if function.get("arguments"):
                        current["arguments"] += function["arguments"]

            for current in tool_calls.values():
                if not isinstance(current.get("name"), str) or not current.get("name"):
                    continue
                yield ModelStreamEvent(
                    type="tool_call",
                    reasoning_content="".join(reasoning_parts) or None,
                    tool_call=ToolCall(
                        call_id=current["id"] or "",
                        tool_name=current["name"],
                        arguments=_parse_tool_arguments(current["arguments"]),
                    ),
                )

            yield ModelStreamEvent(
                type="done",
                stop_reason=choice.get("finish_reason"),
                reasoning_content="".join(reasoning_parts) or None,
                usage=usage,
            )

    def _record_provider_payload_diagnostics(self, messages: list[ModelMessage]) -> None:
        self.last_provider_payload_diagnostics = compact_provider_payload_diagnostics(
            normalize_messages_for_api_with_diagnostics(messages).diagnostics
        )


def _raise_for_status_with_body(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        try:
            body = response.text
        except httpx.ResponseNotRead:
            body = response.read().decode("utf-8", errors="replace")
        logger.error(
            "llm http error: status=%s body=%s",
            response.status_code,
            dump_log_payload(body),
        )
        raise

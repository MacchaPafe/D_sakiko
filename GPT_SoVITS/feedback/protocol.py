"""反馈协议：只从明确允许的字段构造上传副本。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    from chat.chat import Chat
    from character import CharacterAttributes

Json = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
MAX_BODY = 1024 * 1024
SCHEMA: dict[str, Json] = json.loads(Path(__file__).with_name("schema.json").read_text("utf-8"))


def convention_header(request_id: str) -> str:
    """生成公开约定头；它仅过滤扫描器，不是认证。"""
    digest = hashlib.sha256(f"d_sakiko.feedback/v1\n{request_id}".encode()).hexdigest()
    return f"v1:{request_id}:{digest}"


def _project(value: object, definition: dict[str, Json]) -> Json:
    """递归按协议白名单投影，未知字段不进入反馈。"""
    if "$ref" in definition:
        definitions = cast(dict[str, dict[str, Json]], SCHEMA["$defs"])
        return _project(value, definitions[str(definition["$ref"]).split("/")[-1]])
    if isinstance(value, dict):
        properties = cast(dict[str, dict[str, Json]], definition.get("properties", {}))
        required = cast(list[str], definition.get("required", []))
        return {key: _project(value[key], rule) for key, rule in properties.items()
                if key in value and (value[key] is not None or key in required)}
    if isinstance(value, list):
        rule = cast(dict[str, Json], definition.get("items", {}))
        return [_project(item, rule) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("诊断包含无法导出的字段类型。")


def _retrieval(raw: dict[str, object]) -> dict[str, Json]:
    """将检索和工具轨迹变成无任意字典或路径的专用格式。"""
    source = dict(raw)
    arguments = raw.get("arguments")
    if isinstance(arguments, dict) and isinstance(arguments.get("query"), str):
        source["query"] = arguments["query"]
    candidates: list[object] = []
    for field in ("candidates", "thought_candidates", "event_candidates"):
        values = raw.get(field)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    payload = item.get("payload")
                    candidates.append({**(payload if isinstance(payload, dict) else {}), **{k: v for k, v in item.items() if k != "payload"}})
    source["candidates"] = candidates
    for old, new in (("linked_event_ids", "linked_ids"), ("unauthorized_linked_event_ids", "unauthorized_ids"), ("deduplicated_event_ids", "deduplicated_ids")):
        if old in raw:
            source[new] = raw[old]
    injected = raw.get("injected_context")
    if isinstance(injected, dict):
        source["injected_events"] = injected.get("events", [])
        source["injected_thoughts"] = injected.get("thoughts", [])
    result = raw.get("result")
    if isinstance(result, dict):
        source["returned_items"] = result.get("results", [])
        source["injected_events"] = result.get("events", source.get("injected_events", []))
        source["injected_thoughts"] = result.get("thoughts", source.get("injected_thoughts", []))
        if isinstance(result.get("ok"), bool):
            source["ok"] = result["ok"]
    failures = raw.get("source_failures")
    if isinstance(failures, list):
        source["failure_codes"] = [str(item["failure"]["code"]) for item in failures
                                   if isinstance(item, dict) and isinstance(item.get("failure"), dict)
                                   and item["failure"].get("code") in ("temporarily_unavailable", "index_unavailable", "invalid_request", "retrieval_failed")]
    definitions = cast(dict[str, dict[str, Json]], SCHEMA["$defs"])
    return cast(dict[str, Json], _project(source, definitions["retrieval"]))


def project_diagnostics(records: list[dict[str, object]]) -> list[Json]:
    """筛选已有诊断，不重新检索，不透传工具结果或异常日志。"""
    result: list[Json] = []
    definitions = cast(dict[str, dict[str, Json]], SCHEMA["$defs"])
    for raw in records:
        if raw.get("format_version") not in (1, 2):
            raise ValueError("存在不支持的世界书诊断版本，请更新程序后重试。")
        source = dict(raw)
        direct = raw.get("direct_retrieval")
        if isinstance(direct, dict):
            source["direct_retrieval"] = _retrieval(direct)
        elif direct is None:
            source.pop("direct_retrieval", None)
        calls = raw.get("tool_calls", [])
        source["tool_calls"] = [_retrieval(item) for item in calls if isinstance(item, dict)] if isinstance(calls, list) else []
        for field in ("finished_at", "candidate_response", "final_response"):
            if source.get(field) is None:
                source.pop(field, None)
        result.append(_project(source, definitions["diagnostic"]))
    return result


def validate_payload(payload: dict[str, Json]) -> None:
    """验证客户端与服务端共用的结构和跨字段语义。"""
    if payload.get("schema_version") != 2:
        raise ValueError("不支持旧版反馈，请使用当前版本重新提交（需要协议 v2）。")
    if next(Draft202012Validator(SCHEMA).iter_errors(payload), None) is not None:
        raise ValueError("反馈内容不符合上传格式或字段大小限制，请更新程序或提交文字建议。")
    conversation = payload["conversation"]
    if payload["rating"] == "none" and not str(payload["comment"]).strip():
        raise ValueError("请填写反馈文字，或选择赞 / 踩。")
    if conversation is None:
        if payload["target"] is not None or payload["prompt_context"] is not None or payload["worldbook_enabled"] or payload["worldbook_diagnostics"]:
            raise ValueError("文字建议不能附带对话数据。")
    else:
        assert isinstance(conversation, dict)
        messages = cast(list[Json], conversation["messages"])
        # JSON Schema 的 integer 也接受 1.0；按整数索引检查，与 Worker 保持一致。
        target = int(payload["target"]) if payload["target"] is not None else None
        if payload["prompt_context"] is None or (target is not None and (
                target >= len(messages) or cast(dict[str, Json], messages[target])["role"] != "assistant")):
            raise ValueError("反馈目标已改变，请重新发起反馈。")
    if not payload["worldbook_enabled"] and payload["worldbook_diagnostics"]:
        raise ValueError("未启用世界书时不能附带诊断。")
    if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > MAX_BODY:
        raise ValueError("整段对话和诊断超过 1 MiB，无法提交。可以发送文字建议；不会截断对话。")


def freeze_payload(*, app_version: str, rating: str, comment: str, chat: Chat | None = None,
                   character: CharacterAttributes | None = None,
                   base_system_prompt: str = "", runtime_system_prompt: str = "", target: int | None = None,
                   diagnostics: list[dict[str, object]] | None = None) -> tuple[str, bytes]:
    """在提交时一次性冻结正文，之后重试只复用返回的字节。"""
    request_id = str(uuid.uuid4())
    now = int(time.time())
    conversation: Json = None
    enabled = chat is not None and chat.meta.worldbook.enabled
    if chat is not None:
        if character is None:
            raise ValueError("对话反馈缺少角色身份。")
        conversation = {"name": chat.name,
                        "character": {"id": character.character_folder_name, "name": character.character_name},
                        "messages": [{"role": "user" if m.character_name == "User" else "assistant",
                                      "character_name": m.character_name, "text": m.text,
                                      "translation": m.translation, "emotion": m.emotion.as_string()}
                                     for m in chat.message_list]}
    payload: dict[str, Json] = {
        "schema_version": 2, "kind": "feedback", "request_id": request_id, "created_at": now,
        "app_version": app_version, "rating": rating, "comment": comment,
        "target": target, "conversation": conversation,
        "prompt_context": {"source": "rendered_at_feedback", "rendered_at": now,
                           "base_system_prompt": base_system_prompt,
                           "runtime_system_prompt": runtime_system_prompt} if chat else None,
        "worldbook_enabled": enabled, "worldbook_diagnostics": project_diagnostics(diagnostics or []) if enabled else [],
        "consent": "feedback-v1-90d",
    }
    validate_payload(payload)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return request_id, body


def feedback_chat_dict(payload: dict[str, Json], character_name: str | None = None) -> dict[str, Json]:
    """导出与直接导入共用的转换：只取基础提示词，按显式 role 映射消息。"""
    validate_payload(payload)
    conversation = payload["conversation"]
    if not isinstance(conversation, dict):
        raise ValueError("文字建议没有可导出的对话。")
    source_character = cast(dict[str, Json], conversation["character"])
    selected_name = character_name or str(source_character["name"])
    context = cast(dict[str, Json], payload["prompt_context"])
    messages = cast(list[dict[str, Json]], conversation["messages"])
    return {"chat_id": str(uuid.uuid4()), "name": conversation["name"], "type": 1,
            "prompt_config": {"type": "static", "content": context["base_system_prompt"],
                              "character_name": selected_name}, "start_message": "",
            "meta": {}, "message_list": [
                {"character_name": selected_name if m["role"] == "assistant" else "User",
                 "text": m["text"], "translation": m["translation"], "emotion": m["emotion"],
                 "audio_path": "", "attachments": []} for m in messages]}


def export_backup(payload: dict[str, Json], path: Path) -> None:
    """把反馈导出为仅含文本与基础 Static 提示词的现有备份格式。"""
    chat = feedback_chat_dict(payload)
    chat_id = chat["chat_id"]
    manifest = {"format": "d_sakiko.chat_backup", "version": 1, "backup_id": str(uuid.uuid4()),
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "resources": {"audio": {}, "attachment": {}},
                "chats": [{"source_chat_id": chat_id, "chat": chat}], "warnings": []}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))

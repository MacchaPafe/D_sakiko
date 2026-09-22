"""世界书对话设置、角色映射和回合快照生命周期测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chat.chat import Chat, ChatManager, Message
from chat.chat_meta import ChatMeta, WorldbookChatSettings
from emotion_enum import EmotionEnum
from qconfig import DSakikoConfig
from dp_local2 import DSLocalAndVoiceGen
from rag.models import CharacterId
from rag.worldbook.runtime.catalog import WorldbookRootCatalog
from rag.worldbook.runtime.conversation import (
    freeze_worldbook_snapshot,
    normalize_character_knowledge_mappings,
)
from rag.worldbook.runtime.models import (
    DirectWorldbookContext,
    DirectThought,
    KnownStoryEvent,
    WorldbookKnowledgeResult,
    WorldbookTurnSnapshot,
)


class _FakeWorldbookService:
    """为直接注入测试返回固定角色观点。"""

    def __init__(self) -> None:
        """初始化查询记录。"""

        self.queries: list[str] = []

    def direct_context(
        self,
        context: WorldbookTurnSnapshot,
        query: str,
        current_user_text: str,
    ) -> WorldbookKnowledgeResult:
        """记录参数并返回一条模型安全事件和观点。"""

        del context, current_user_text
        self.queries.append(query)
        return WorldbookKnowledgeResult(
            knowledge=DirectWorldbookContext(
                events=[
                    KnownStoryEvent(
                        title="初次相遇",
                        summary="爱音摔倒后得到灯递来的创可贴。",
                        participant_names=["爱音", "灯"],
                    )
                ],
                thoughts=[
                    DirectThought(
                        character_name="爱音",
                        thought_text="爱音想继续组建乐队。",
                        epistemic_status="believes",
                    )
                ],
            )
        )

    def close(self) -> None:
        """测试服务没有外部资源。"""


def _snapshot(episode: int = 2) -> WorldbookTurnSnapshot:
    """创建可序列化的 MyGO 回合快照。"""

    return WorldbookTurnSnapshot(
        root_package_id="official.bang_dream.its_mygo",
        root_package_version="0.1.0",
        package_ids=["official.bang_dream.its_mygo"],
        package_versions={"official.bang_dream.its_mygo": "0.1.0"},
        package_depths={"official.bang_dream.its_mygo": 0},
        character_id="anon",
        series_id="its_mygo",
        timeline_id="bang_dream_original",
        canon_branch="main",
        current_time=4099,
        story_year=3,
        episode=episode,
    )


def _message(snapshot: WorldbookTurnSnapshot | None = None) -> Message:
    """创建一条真实用户消息。"""

    return Message(
        character_name="User",
        text="你好",
        translation="",
        emotion=EmotionEnum.HAPPINESS,
        audio_path="",
        worldbook_snapshot=snapshot,
    )


class WorldbookConversationStateTest(unittest.TestCase):
    """验证世界书状态在配置、消息和复制流程中的生命周期。"""

    def test_default_chat_settings_are_omitted_and_legacy_loads_disabled(self) -> None:
        """旧 ChatMeta 应默认关闭世界书，默认值不应污染存档。"""

        meta = ChatMeta.from_dict({})

        self.assertEqual(meta.worldbook, WorldbookChatSettings())
        self.assertNotIn("worldbook", meta.to_dict())

    def test_chat_settings_roundtrip_and_reject_invalid_episode(self) -> None:
        """世界书根包与集数应往返，非法集数应安全视为未设置。"""

        configured = ChatMeta(
            worldbook=WorldbookChatSettings(
                enabled=True,
                root_package_id="official.bang_dream.its_mygo",
                episode=5,
            )
        )
        restored = ChatMeta.from_dict(configured.to_dict())
        invalid = ChatMeta.from_dict(
            {"worldbook": {"enabled": True, "root_package_id": "root", "episode": 14}}
        )

        self.assertEqual(restored.worldbook, configured.worldbook)
        self.assertIsNone(invalid.worldbook.episode)

    def test_message_snapshot_roundtrip_and_legacy_compatibility(self) -> None:
        """Message 应保留快照，旧消息缺少字段时应继续加载。"""

        message = _message(_snapshot())
        restored = Message.from_dict(message.as_dict())
        legacy = message.as_dict()
        legacy.pop("worldbook_snapshot")

        self.assertEqual(restored.worldbook_snapshot, message.worldbook_snapshot)
        self.assertIsNone(Message.from_dict(legacy).worldbook_snapshot)

    def test_freeze_supports_builtin_folder_and_manual_custom_mapping(self) -> None:
        """内置同名文件夹可直连，定制文件夹应使用全局手动映射。"""

        app_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as state_directory:
            catalog = WorldbookRootCatalog(
                app_root / "GPT_SoVITS" / "rag" / "worldbooks" / "official",
                Path(state_directory),
            )
            builtin = freeze_worldbook_snapshot(
                catalog,
                enabled=True,
                root_package_id="official.bang_dream.its_mygo",
                episode=2,
                character_folder_name="anon",
                mappings={},
            )
            custom = freeze_worldbook_snapshot(
                catalog,
                enabled=True,
                root_package_id="official.bang_dream.its_mygo",
                episode=2,
                character_folder_name="custom-anon",
                mappings={"custom-anon": CharacterId.ANON},
            )
            unmapped = freeze_worldbook_snapshot(
                catalog,
                enabled=True,
                root_package_id="official.bang_dream.its_mygo",
                episode=2,
                character_folder_name="custom-unknown",
                mappings={},
            )

        self.assertEqual(builtin.snapshot.character_id if builtin.snapshot else None, CharacterId.ANON)
        self.assertEqual(custom.snapshot.character_id if custom.snapshot else None, CharacterId.ANON)
        self.assertIsNone(unmapped.snapshot)
        self.assertEqual(unmapped.disabled_reason, "unmapped_character")

    def test_mapping_normalization_drops_invalid_entries(self) -> None:
        """配置映射应只保留非空文件夹和合法 CharacterId。"""

        mappings = normalize_character_knowledge_mappings(
            {"custom": "soyo", "bad": "unknown", "": "anon", 1: "anon"}
        )

        self.assertEqual(mappings, {"custom": CharacterId.SOYO})

    def test_clone_and_fork_keep_message_snapshot(self) -> None:
        """对话复制和分叉应通过 Message 序列化自然保留快照。"""

        source = Chat(message_list=[_message(_snapshot()), _message()])
        manager = ChatManager([source])

        cloned = manager.clone_chat(source.chat_id, copy_attachments=False)
        forked = manager.clone_chat(
            source.chat_id,
            fork_after_message_index=0,
            copy_attachments=False,
        )

        self.assertEqual(cloned.message_list[0].worldbook_snapshot, _snapshot())
        self.assertEqual(forked.message_list[0].worldbook_snapshot, _snapshot())

    def test_qconfig_declares_mapping_and_diagnostic_flags(self) -> None:
        """全局配置应声明角色映射、诊断持久化和首次告知标记。"""

        self.assertTrue(hasattr(DSakikoConfig, "worldbook_character_mappings"))
        self.assertTrue(hasattr(DSakikoConfig, "worldbook_diagnostics_persistence"))
        self.assertTrue(hasattr(DSakikoConfig, "worldbook_diagnostics_disclosure_seen"))

    def test_direct_query_uses_current_and_previous_complete_round(self) -> None:
        """直接检索文本只应包含当前消息和上一完整用户/角色轮次。"""

        chat = Chat(
            message_list=[
                Message("User", "更早问题", "", EmotionEnum.HAPPINESS, ""),
                Message("爱音", "更早回答", "", EmotionEnum.HAPPINESS, ""),
                Message("User", "上一轮问题", "", EmotionEnum.HAPPINESS, ""),
                Message("爱音", "上一轮回答一", "", EmotionEnum.HAPPINESS, ""),
                Message("爱音", "上一轮回答二", "", EmotionEnum.HAPPINESS, ""),
                Message("User", "当前问题", "", EmotionEnum.HAPPINESS, ""),
            ]
        )

        query = DSLocalAndVoiceGen._build_worldbook_query_text(chat)

        self.assertIn("当前问题", query)
        self.assertIn("上一轮问题", query)
        self.assertIn("上一轮回答一", query)
        self.assertIn("上一轮回答二", query)
        self.assertNotIn("更早问题", query)
        self.assertNotIn("更早回答", query)

    def test_direct_context_is_temporary_user_message_before_runtime_controls(self) -> None:
        """直接知识应插在真实输入后、运行控制前，且不修改 Chat 历史。"""

        chat = Chat(message_list=[_message(_snapshot())])
        manager = ChatManager([chat])
        backend = DSLocalAndVoiceGen.__new__(DSLocalAndVoiceGen)
        backend.chat_manager = manager
        backend.current_chat_id = chat.chat_id
        service = _FakeWorldbookService()
        backend._worldbook_service = service
        messages: list[dict[str, object]] = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "[User]: 你好"},
            {
                "role": "user",
                "content": "<runtime_controls>\nreply_language: zh_only\n</runtime_controls>",
            },
        ]

        prepared = backend._inject_direct_worldbook_context(
            messages,
            _snapshot(),
            "你好",
        )

        self.assertEqual(len(messages), 3)
        self.assertEqual(len(chat.message_list), 1)
        self.assertEqual(prepared[-2]["role"], "user")
        self.assertIn("<worldbook_context>", str(prepared[-2]["content"]))
        self.assertIn('"events"', str(prepared[-2]["content"]))
        self.assertIn('"thoughts"', str(prepared[-2]["content"]))
        self.assertNotIn("entry_id", str(prepared[-2]["content"]))
        self.assertTrue(str(prepared[-1]["content"]).startswith("<runtime_controls>"))

    def test_worldbook_system_instruction_contains_rules_but_no_dynamic_snapshot(self) -> None:
        """稳定规则可进入 system，但不得公开包、集数或内部剧情坐标。"""

        messages: list[dict[str, object]] = [
            {"role": "system", "content": "基础规则"},
            {"role": "user", "content": "你好"},
        ]

        DSLocalAndVoiceGen._append_worldbook_runtime_instruction(messages)

        system_text = str(messages[0]["content"])
        self.assertIn("search_worldbook_lore", system_text)
        self.assertIn("<worldbook_context>", system_text)
        self.assertNotIn("official.bang_dream.its_mygo", system_text)
        self.assertNotIn("4099", system_text)
        self.assertEqual(len(messages), 2)


if __name__ == "__main__":
    unittest.main()

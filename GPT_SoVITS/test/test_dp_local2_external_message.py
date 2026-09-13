from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from dp_local2 import DSLocalAndVoiceGen


class ExternalUserMessageTest(unittest.TestCase):
    def test_model_error_does_not_remove_message_appended_by_caller(self) -> None:
        runtime = DSLocalAndVoiceGen.__new__(DSLocalAndVoiceGen)
        runtime._rollback_user_message_on_error = False
        runtime._format_exception_details = Mock(return_value="details")
        runtime._emit_turn_error = Mock()
        runtime._clear_failed_turn_state = Mock()

        with patch("dp_local2.logger.error"):
            runtime._report_model_exception(
                Mock(),
                "chat_id",
                "turn_id",
                Mock(),
                "请求失败",
                RuntimeError("failed"),
            )

        runtime._clear_failed_turn_state.assert_called_once()
        self.assertFalse(runtime._clear_failed_turn_state.call_args.kwargs["rollback_user_message"])


if __name__ == "__main__":
    unittest.main()

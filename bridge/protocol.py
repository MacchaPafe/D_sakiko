"""Wire format for the Electron business-event stream."""

from __future__ import annotations

import json
from typing import Any


def create_message(message_type: str, data: Any) -> str:
    return json.dumps(
        {"type": str(message_type), "data": data},
        ensure_ascii=False,
        separators=(",", ":"),
    )

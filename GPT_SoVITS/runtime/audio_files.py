from __future__ import annotations

import os
import uuid


def allocate_output_wav_path(output_dir: str) -> str:
    """为本次生成分配一个新的输出音频路径。"""
    os.makedirs(output_dir, exist_ok=True)
    while True:
        output_wav_path = os.path.join(output_dir, f"output_{uuid.uuid4().hex}.wav")
        try:
            with open(output_wav_path, "xb"):
                pass
        except FileExistsError:
            continue
        return output_wav_path

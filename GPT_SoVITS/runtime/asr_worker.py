"""本地 ASR 独立进程入口，仅进行已有 Whisper 模型的推理。"""

import base64
import json
import sys

PREFIX = "DS_ASR_JSON:"


def reply(payload):
    print(PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def main():
    # CTranslate2 可选模型转换器会尝试导入 Torch；推理不需要这些转换器。
    # 在这个只做 CT2 推理的独立进程中禁用可选 Torch，避免 macOS 双 libiomp。
    sys.modules["torch"] = None
    try:
        import numpy as np
        from faster_whisper import WhisperModel

        model = WhisperModel(sys.argv[1], device="cpu", compute_type="int8")
        reply({"ready": True})
    except Exception as error:
        reply({"error": str(error)})
        return
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if command.get("type") == "exit":
                return
            audio = np.frombuffer(
                base64.b64decode(command["audio"]), dtype="<f4"
            ).copy()
            segments, _ = model.transcribe(audio, beam_size=5, language="zh")
            reply({"text": "".join(segment.text for segment in segments)})
        except Exception as error:
            reply({"error": str(error)})


if __name__ == "__main__":
    main()

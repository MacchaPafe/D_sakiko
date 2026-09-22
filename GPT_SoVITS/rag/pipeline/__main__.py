"""支持 `python -m rag.pipeline` 形式的入口。"""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())

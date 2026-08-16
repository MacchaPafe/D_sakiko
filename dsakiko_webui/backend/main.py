from __future__ import annotations

import uvicorn

from .app import create_app
from .assets import PROJECT_ROOT
from GPT_SoVITS.runtime.runtime_lock import RuntimeLockBusy, acquire_runtime_lock


app = create_app()


if __name__ == "__main__":
    try:
        app.state.runtime_lease = acquire_runtime_lock(PROJECT_ROOT, "web")
    except RuntimeLockBusy as exc:
        print(str(exc))
        raise SystemExit(1)
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)

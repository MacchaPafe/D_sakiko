"""The sole Python owner of Live2D behaviour state for a runtime session."""
from __future__ import annotations

import time

from live2d_support.behavior_scheduler import SharedBehaviorScheduler
from live2d_support.sakiko_conversion import SharedSakikoConversion
from live2d_support.shared_behavior import SharedLive2DBehavior


class AuthoritativeLive2DOwner:
    """Own state exactly once; renderer adapters may only consume its output."""

    def __init__(self, *, clock=None, rng=None) -> None:
        clock = clock or time.monotonic
        self.behavior = SharedLive2DBehavior(rng=rng)
        self.scheduler = SharedBehaviorScheduler(clock=clock, rng=rng)
        self.sakiko_conversion = SharedSakikoConversion(rng=rng)

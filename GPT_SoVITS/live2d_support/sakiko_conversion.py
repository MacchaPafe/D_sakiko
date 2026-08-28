"""Master Sakiko black/white/mask decisions, independent of renderer reloads."""
from __future__ import annotations

from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class SakikoConversionDecision:
    model_target: str
    semantic_expression: str | None
    motion_group: str
    priority: int
    purpose: str
    fixed_index: int | None = None
    resulting_is_black: bool | None = None
    resulting_mask_on: bool | None = None


class SharedSakikoConversion:
    """Owns master-only conversion state; adapters only reload and execute."""

    def __init__(self, rng: Random | None = None) -> None:
        self._rng = rng or Random()
        self.is_black = True
        self.mask_on = True

    def decide(self, conversion) -> SakikoConversionDecision:
        if conversion == "maskoff":
            return self._toggle_mask()
        if conversion:
            next_mask_on = self._rng.random() < 0.5
            return SakikoConversionDecision(
                "black", "serious",
                "change_character" if next_mask_on else "change_character_maskoff",
                2, "sakiko_black", None, True, next_mask_on,
            )
        return SakikoConversionDecision("white", "idle", "change_character", 2, "sakiko_white", None, False, self.mask_on)

    def _toggle_mask(self) -> SakikoConversionDecision:
        if not self.is_black:
            return SakikoConversionDecision("current", None, "text_generating", 3, "sakiko_white_toggle", 0, False, self.mask_on)
        group = "change_character_maskoff" if self.mask_on else "maskon"
        next_mask_on = not self.mask_on
        return SakikoConversionDecision("current", None, group, 3, "sakiko_mask_toggle", None, self.is_black, next_mask_on)

    def preview(self, conversion) -> SakikoConversionDecision:
        """Resolve a decision without mutating persistent business state."""
        import copy
        shadow = copy.copy(self)
        return shadow.decide(conversion)

    def commit(self, decision: SakikoConversionDecision) -> None:
        if decision.resulting_is_black is not None:
            self.is_black = decision.resulting_is_black
        if decision.resulting_mask_on is not None:
            self.mask_on = decision.resulting_mask_on

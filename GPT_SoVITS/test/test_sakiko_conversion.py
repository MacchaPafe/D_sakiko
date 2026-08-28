from __future__ import annotations
import os, sys, unittest
from random import Random
root=os.path.abspath(os.path.join(os.path.dirname(__file__),"..")); sys.path.insert(0,root) if root not in sys.path else None
from live2d_support.sakiko_conversion import SharedSakikoConversion

class SakikoConversionTest(unittest.TestCase):
 def test_black_conversion_owns_the_only_initial_mask_roll(self):
  state=SharedSakikoConversion(Random(1)); decision=state.decide(True)
  self.assertEqual((decision.model_target,decision.semantic_expression,decision.motion_group,decision.priority), ("black","serious","change_character",2))
  self.assertTrue(state.mask_on)
 def test_white_and_mask_toggle_match_master_groups(self):
  state=SharedSakikoConversion(Random(1)); white=state.decide(False)
  self.assertEqual((white.model_target,white.semantic_expression,white.motion_group), ("white","idle","change_character"))
  state.commit(white)
  self.assertEqual((state.decide("maskoff").motion_group,state.decide("maskoff").fixed_index), ("text_generating",0))
 def test_black_mask_toggle_flips_after_decision(self):
  state=SharedSakikoConversion(Random(1)); state.commit(state.decide(True))
  first = state.decide("maskoff"); state.commit(first)
  self.assertEqual(first.motion_group,"change_character_maskoff")
  self.assertEqual(state.decide("maskoff").motion_group,"maskon")
if __name__=='__main__': unittest.main()

from __future__ import annotations
import os, sys, unittest
from multiprocessing import Value
from queue import Empty, Queue
root=os.path.abspath(os.path.join(os.path.dirname(__file__),"..")); sys.path.insert(0,root) if root not in sys.path else None
from live2d_support.thinking_state import ThinkingStateQueue

class ThinkingStateQueueTest(unittest.TestCase):
 def setUp(self): self.events=Queue(); self.queue=ThinkingStateQueue(Queue(), self.events, Value("i", 0))
 def test_only_zero_one_edges_are_published(self):
  self.queue.put("a"); self.queue.put("b"); self.assertTrue(self.events.get_nowait()["data"]["active"])
  with self.assertRaises(Empty): self.events.get_nowait()
  self.queue.get(); self.assertTrue(self.events.empty()); self.queue.get(); self.assertFalse(self.events.get_nowait()["data"]["active"])
 def test_failed_get_does_not_emit_false(self):
  with self.assertRaises(Empty): self.queue.get_nowait()
  self.assertTrue(self.events.empty())
if __name__=='__main__': unittest.main()

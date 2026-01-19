import os
import unittest

import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from emotion_enum import EmotionEnum


class EmotionEnumTest(unittest.TestCase):
    def test_emotion_enum(self):
        a = EmotionEnum.from_string("happiness")
        b = EmotionEnum.from_string("LABEL_0")
        self.assertEqual(a, EmotionEnum.HAPPINESS)
        self.assertEqual(b, EmotionEnum.HAPPINESS)
        self.assertEqual(a, b)
        self.assertEqual(a.as_label(), "LABEL_0")
        self.assertEqual(a.as_string(), "happiness")


if __name__ == '__main__':
    unittest.main()

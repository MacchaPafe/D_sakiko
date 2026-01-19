# 此文件存储了两种情感表示的相互映射
from enum import Enum


class EmotionEnum(Enum):
    """
    情感类型的定义。由于情感标签在数据集中存在不同的表示形式（例如 LABEL_0 = happiness），此枚举旨在接受任意形式的输入并提供统一的输出接口。
    例如：

    >>> a = EmotionEnum.from_string("happiness")
    >>> a == EmotionEnum.HAPPINESS
    True
    >>> b = EmotionEnum.from_string("LABEL_0")
    >>> b == EmotionEnum.HAPPINESS
    True
    >>> a == b
    True
    >>> a.as_label()
    'LABEL_0'
    >>> a.as_string()
    'happiness'
    """
    HAPPINESS = 0
    SADNESS = 1
    ANGER = 2
    DISGUST = 3
    LIKE = 4
    SURPRISE = 5
    FEAR = 6

    @classmethod
    def from_string(cls, emotion_str):
        mapping = {
            "happiness": cls.HAPPINESS,
            "label_0": cls.HAPPINESS,
            "sadness": cls.SADNESS,
            "label_1": cls.SADNESS,
            "anger": cls.ANGER,
            "label_2": cls.ANGER,
            "disgust": cls.DISGUST,
            "label_3": cls.DISGUST,
            "like": cls.LIKE,
            "label_4": cls.LIKE,
            "surprise": cls.SURPRISE,
            "label_5": cls.SURPRISE,
            "fear": cls.FEAR,
            "label_6": cls.FEAR,
        }
        return mapping.get(emotion_str.lower(), EmotionEnum.HAPPINESS)

    def as_label(self):
        reverse_mapping = {
            self.HAPPINESS: "LABEL_0",
            self.SADNESS: "LABEL_1",
            self.ANGER: "LABEL_2",
            self.DISGUST: "LABEL_3",
            self.LIKE: "LABEL_4",
            self.SURPRISE: "LABEL_5",
            self.FEAR: "LABEL_6",
        }
        return reverse_mapping.get(self, "LABEL_0")

    def as_string(self):
        reverse_mapping = {
            self.HAPPINESS: "happiness",
            self.SADNESS: "sadness",
            self.ANGER: "anger",
            self.DISGUST: "disgust",
            self.LIKE: "like",
            self.SURPRISE: "surprise",
            self.FEAR: "fear",
        }
        return reverse_mapping.get(self, "happiness")

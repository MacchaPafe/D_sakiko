# 原始代码来自：https://github.com/RVC-Boss/GPT-SoVITS/blob/main/GPT_SoVITS/text/g2pw/g2pw.py
# This code is modified from https://github.com/mozillazg/pypinyin-g2pW

from __future__ import annotations

from speech.gpt_sovits_resources import BertFiles, G2pwFiles

from typing import Dict, List, cast

import pickle
import os

from pypinyin.constants import RE_HANS
from pypinyin.core import Pinyin, Style
from pypinyin.seg.simpleseg import simple_seg
from pypinyin.converter import UltimateConverter
from pypinyin.contrib.tone_convert import to_tone
from .onnx_api import G2PWOnnxConverter

current_file_path = os.path.dirname(__file__)
PRONUNCIATION_DICT_PATH = os.path.join(current_file_path, "polyphonic.pickle")


class G2PWPinyin(Pinyin):
    def __init__(
        self,
        files: G2pwFiles,
        bert_files: BertFiles,
        enable_non_tradional_chinese: bool = True,
        v_to_u: bool = False,
        neutral_tone_with_five: bool = False,
        tone_sandhi: bool = False,
        **kwargs: object,
    ) -> None:
        """将各用途文件传递给 G2PW，分词器使用同一组 BERT 资源。"""
        self._g2pw = G2PWOnnxConverter(
            files=files,
            bert_files=bert_files,
            style="pinyin",
            enable_non_tradional_chinese=enable_non_tradional_chinese,
        )
        self._converter = Converter(
            self._g2pw,
            v_to_u=v_to_u,
            neutral_tone_with_five=neutral_tone_with_five,
            tone_sandhi=tone_sandhi,
        )

    def get_seg(self, **kwargs):
        return simple_seg


class Converter(UltimateConverter):
    def __init__(self, g2pw_instance, v_to_u=False, neutral_tone_with_five=False, tone_sandhi=False, **kwargs):
        super(Converter, self).__init__(
            v_to_u=v_to_u, neutral_tone_with_five=neutral_tone_with_five, tone_sandhi=tone_sandhi, **kwargs
        )

        self._g2pw = g2pw_instance

    def convert(self, words, style, heteronym, errors, strict, **kwargs):
        pys = []
        if RE_HANS.match(words):
            pys = self._to_pinyin(words, style=style, heteronym=heteronym, errors=errors, strict=strict)
            post_data = self.post_pinyin(words, heteronym, pys)
            if post_data is not None:
                pys = post_data

            pys = self.convert_styles(pys, words, style, heteronym, errors, strict)

        else:
            py = self.handle_nopinyin(words, style=style, errors=errors, heteronym=heteronym, strict=strict)
            if py:
                pys.extend(py)

        return _remove_dup_and_empty(pys)

    def _to_pinyin(self, han, style, heteronym, errors, strict, **kwargs):
        pinyins = []

        g2pw_pinyin = self._g2pw(han)

        if not g2pw_pinyin:  # g2pw 不支持的汉字改为使用 pypinyin 原有逻辑
            return super(Converter, self).convert(han, Style.TONE, heteronym, errors, strict, **kwargs)

        for i, item in enumerate(g2pw_pinyin[0]):
            if item is None:  # g2pw 不支持的汉字改为使用 pypinyin 原有逻辑
                py = super(Converter, self).convert(han[i], Style.TONE, heteronym, errors, strict, **kwargs)
                pinyins.extend(py)
            else:
                pinyins.append([to_tone(item)])

        return pinyins


def _remove_dup_items(lst, remove_empty=False):
    new_lst = []
    for item in lst:
        if remove_empty and not item:
            continue
        if item not in new_lst:
            new_lst.append(item)
    return new_lst


def _remove_dup_and_empty(lst_list):
    new_lst_list = []
    for lst in lst_list:
        lst = _remove_dup_items(lst, remove_empty=True)
        if lst:
            new_lst_list.append(lst)
        else:
            new_lst_list.append([""])

    return new_lst_list


def get_dict() -> Dict[str, List[str]]:
    """读取随推理代码发布的静态发音词典，不在只读安装目录重建缓存。"""
    with open(PRONUNCIATION_DICT_PATH, "rb") as stream:
        return cast(Dict[str, List[str]], pickle.load(stream))


def correct_pronunciation(word, word_pinyins):
    new_pinyins = pp_dict.get(word, "")
    if new_pinyins == "":
        for idx, w in enumerate(word):
            w_pinyin = pp_dict.get(w, "")
            if w_pinyin != "":
                word_pinyins[idx] = w_pinyin[0]
        return word_pinyins
    else:
        return new_pinyins


pp_dict = get_dict()

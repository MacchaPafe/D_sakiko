# 配置 GPT-SoVITS 语音合成模型参数的相关 UI
import contextlib

from PyQt5.QtWidgets import QVBoxLayout

with contextlib.redirect_stdout(None):
    from qfluentwidgets import (
        BodyLabel,
        ToolTipFilter,
        ToolTipPosition,
        ComboBox,
        SettingCardGroup,
        ComboBoxSettingCard,
        FluentIcon,
    )

from ..custom_widgets.transparent_scroll_area import TransparentScrollArea
from qconfig import d_sakiko_config


class GPTSoVITSArea(TransparentScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.v_box_layout = QVBoxLayout(self.view)

        # 统一：用 SettingCardGroup + ComboBoxSettingCard 来承载设置项（风格与 custom_setting_area.py 对齐）
        self.audio_setting_group = SettingCardGroup(self.tr("音频"), self.view)
        self.audio_setting_group.titleLabel.setVisible(False)

        self.delete_audio_card = ComboBoxSettingCard(
            d_sakiko_config.delete_audio_cache_on_exit,
            FluentIcon.DELETE,
            self.tr("退出时删除缓存音频"),
            self.tr("删除可节省空间，但未备份的历史消息将无法回放对应音频"),
            texts=[self.tr("不删除"), self.tr("删除")],
            parent=self.audio_setting_group,
        )

        # 是否启用 fp16 推理（注意：配置项存的是 enable_fp32_inference，逻辑与 UI 反过来）
        self.fp_precision_card = ComboBoxSettingCard(
            d_sakiko_config.enable_fp32_inference,
            FluentIcon.SPEED_HIGH,
            self.tr("推理精度"),
            self.tr("FP16 更快，FP32 更稳（此项修改需要重启生效）"),
            texts=[self.tr("FP32"), self.tr("FP16")],
            parent=self.audio_setting_group,
        )

        self.inference_step_card = ComboBoxSettingCard(
            d_sakiko_config.sovits_inference_sampling_steps,
            FluentIcon.TILES,
            self.tr("推理采样步数"),
            self.tr("降低采样步数可降低生成时间，但生成质量也会降低\n步数越高，音质越好，推理时间也会相应增加"),
            texts=['4', '8', '16', '32'],
            parent=self.audio_setting_group
        )

        self.audio_setting_group.addSettingCard(self.delete_audio_card)
        self.audio_setting_group.addSettingCard(self.fp_precision_card)
        self.audio_setting_group.addSettingCard(self.inference_step_card)

        self.v_box_layout.addWidget(self.audio_setting_group)

        self.setMinimumWidth(500)
        # 加载初始内容
        self.load_config_to_ui()

    def load_config_to_ui(self):
        """
        从 d_sakiko_config 唯一对象中加载配置到 ui
        """
        pass

    def save_ui_to_config(self) -> bool:
        """
        存储当前的设置到 配置文件中，其实没有可保存的（
        """
        return True

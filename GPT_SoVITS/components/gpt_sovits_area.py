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
from ..qconfig import d_sakiko_config


class GPTSoVITSArea(TransparentScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self.view)

        # 统一：用 SettingCardGroup + ComboBoxSettingCard 来承载设置项（风格与 custom_setting_area.py 对齐）
        self.audioSettingGroup = SettingCardGroup(self.tr("音频"), self.view)

        self.deleteAudioCard = ComboBoxSettingCard(
            d_sakiko_config.delete_audio_cache_on_exit,
            FluentIcon.DELETE,
            self.tr("退出时删除缓存音频"),
            self.tr("删除可节省空间，但未备份的历史消息将无法回放对应音频"),
            texts=[self.tr("不删除"), self.tr("删除")],
            parent=self.audioSettingGroup,
        )
        # BoolConfigItem -> 下拉框索引的映射（0=False, 1=True）
        with contextlib.suppress(Exception):
            self.deleteAudioCard.comboBox.setCurrentIndex(1 if d_sakiko_config.delete_audio_cache_on_exit.value else 0)

        # 是否启用 fp16 推理（注意：配置项存的是 enable_fp32_inference，逻辑与 UI 反过来）
        self.fpPrecisionCard = ComboBoxSettingCard(
            d_sakiko_config.enable_fp32_inference,
            FluentIcon.SPEED_HIGH,
            self.tr("推理精度"),
            self.tr("FP16 更快，FP32 更稳（此项修改需要重启生效）"),
            texts=[self.tr("FP16"), self.tr("FP32")],
            parent=self.audioSettingGroup,
        )
        # 这里 UI = [FP16, FP32]，而配置项含义是 enable_fp32_inference
        # index 0 -> enable_fp32_inference=False
        # index 1 -> enable_fp32_inference=True
        with contextlib.suppress(Exception):
            self.fpPrecisionCard.comboBox.setCurrentIndex(1 if d_sakiko_config.enable_fp32_inference.value else 0)

        self.audioSettingGroup.addSettingCard(self.deleteAudioCard)
        self.audioSettingGroup.addSettingCard(self.fpPrecisionCard)

        self.vBoxLayout.addWidget(self.audioSettingGroup)

        # 推理步数（保留原有 ComboBox 逻辑）
        self.inferenceStepLabel = BodyLabel(self.tr("可设置GPT-SoVITS推理采样步数：（鼠标悬浮查看说明）"), self)
        self.inferenceStepLabel.setToolTip(
            self.tr(
                "降低采样步数可降低生成时间，但生成质量也会降低；步数越高，音质越好，推理时间也会相应增加。"
                "建议根据自己的硬件性能和需求进行调整。默认是16。"
            )
        )  # 共有四档，4、8、16、32
        self.inferenceStepLabel.installEventFilter(
            ToolTipFilter(self.inferenceStepLabel, showDelay=300, position=ToolTipPosition.TOP)
        )

        self.sample_step_combobox = ComboBox()
        self.sample_step_combobox.addItems(["4", "8", "16", "32"])
        # 读取并且显示当前的采样步数设置
        current_step = str(d_sakiko_config.sovits_inference_sampling_steps.value)
        index = self.sample_step_combobox.findText(current_step)
        # 如果存储的是 4、8、16、32 中的值，则设置为对应的选项
        if index >= 0:
            self.sample_step_combobox.setCurrentIndex(index)
        self.vBoxLayout.addWidget(self.inferenceStepLabel)
        self.vBoxLayout.addWidget(self.sample_step_combobox)

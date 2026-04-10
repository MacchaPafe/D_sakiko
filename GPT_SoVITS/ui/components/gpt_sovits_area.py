# 配置 GPT-SoVITS 语音合成模型参数的相关 UI
import contextlib
import platform

from PyQt5.QtWidgets import QVBoxLayout
from PyQt5.QtCore import pyqtSlot, Qt, QTimer

with contextlib.redirect_stdout(None):
    from qfluentwidgets import (
        SettingCardGroup,
        ComboBoxSettingCard,
        FluentIcon, MessageBox, RangeSettingCard,
        TeachingTip, TeachingTipTailPosition
)

from ..custom_widgets.transparent_scroll_area import TransparentScrollArea
from ..custom_widgets.custom_switch_setting_card import SwitchSettingCard
from ..threads.gpu_detect_thread import CUDADetectThread, MPSDetectThread
from ..threads.memory_detect_thread import MemoryDetectThread
from qconfig import d_sakiko_config


class GPTSoVITSArea(TransparentScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.v_box_layout = QVBoxLayout(self.view)

        # 统一：用 SettingCardGroup + ComboBoxSettingCard 来承载设置项（风格与 custom_setting_area.py 对齐）
        self.audio_setting_group = SettingCardGroup(self.tr("音频"), self.view)
        self.audio_setting_group.titleLabel.setVisible(False)

        self.delete_audio_card = SwitchSettingCard(
            FluentIcon.DELETE,
            self.tr("退出时删除缓存音频"),
            self.tr("删除可节省空间，但未备份的历史消息将无法回放对应音频"),
            d_sakiko_config.delete_audio_cache_on_exit,
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
            self.tr("降低采样步数可降低生成时间，但生成质量也会降低\n步数越高，音质越好，推理时间也会相应增加（重启后生效）"),
            texts=['4', '8', '16', '32'],
            parent=self.audio_setting_group
        )

        self.cuda_setting_card = SwitchSettingCard(
            FluentIcon.IOT,
            self.tr("启用 CUDA（NVIDIA GPU）"),
            self.tr("启用 NVIDIA GPU 进行语音合成。推荐启用以获得更快的推理速度（重启后生效）"),
            d_sakiko_config.cuda_enabled,
            parent=self.audio_setting_group,
        )
        self.cuda_setting_card.checkedChanged.connect(self._on_cuda_setting_changed)
        self.cuda_setting_card.setVisible(False)  # 默认隐藏
        self.cuda_setting_card.setEnabled(False)  # 默认不可用，等待检测线程结果
        self.mps_setting_card = SwitchSettingCard(
            FluentIcon.IOT,
            self.tr("启用 MPS（Apple GPU）"),
            self.tr("启用 Apple GPU 进行语音合成（重启后生效）"),
            d_sakiko_config.mps_enabled,
            parent=self.audio_setting_group,
        )
        self.mps_setting_card.checkedChanged.connect(self._on_mps_setting_changed)
        self.mps_setting_card.setVisible(False)  # 默认隐藏
        self.mps_setting_card.setEnabled(False)  # 默认不可用，等待检测线程结果

        system = platform.system()
        if platform.system() == "Windows" or system == "Linux":
            self.cuda_setting_card.setVisible(True)
            self.cuda_thread = CUDADetectThread()
            self.cuda_thread.cuda_exist_signal.connect(self._on_cuda_availability_checked)
            self.cuda_thread.start()
        elif system == "Darwin":
            self.mps_setting_card.setVisible(True)
            self.mps_thread = MPSDetectThread()
            self.mps_thread.mps_exist_signal.connect(self._on_mps_availability_checked)
            self.mps_thread.start()

        self.max_model_count_card = RangeSettingCard(
            d_sakiko_config.max_loaded_voice_models,
            FluentIcon.MUSIC,
            self.tr("同时加载模型数量上限"),
            self.tr("加载过多模型会占用大量内存（重启后生效）"),
            parent=self.audio_setting_group
        )
        self.max_model_count_card.slider.setMinimumWidth(150)
        self.max_model_count_card.valueChanged.connect(self._on_max_model_count_changed)
        
        self.warning_timer = QTimer(self)
        self.warning_timer.setSingleShot(True)
        self.warning_timer.timeout.connect(self._show_memory_warning)
        self.active_teaching_tip = None
        
        self.memory_size_gb = None
        self.memory_thread = MemoryDetectThread()
        self.memory_thread.memory_size_signal.connect(self._on_memory_size_checked)
        self.memory_thread.start()

        self.audio_setting_group.addSettingCard(self.delete_audio_card)
        self.audio_setting_group.addSettingCard(self.fp_precision_card)
        self.audio_setting_group.addSettingCard(self.inference_step_card)
        self.audio_setting_group.addSettingCard(self.cuda_setting_card)
        self.audio_setting_group.addSettingCard(self.mps_setting_card)
        self.audio_setting_group.addSettingCard(self.max_model_count_card)

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

    @pyqtSlot(bool)
    def _on_cuda_availability_checked(self, available: bool):
        self.cuda_setting_card.setEnabled(available)
        if available:
            self.cuda_setting_card.setVisible(True)
            self.cuda_setting_card.setEnabled(True)
        else:
            self.cuda_setting_card.setEnabled(False)
            self.cuda_setting_card.setChecked(False)
    
    @pyqtSlot(bool)
    def _on_mps_availability_checked(self, available: bool):
        self.mps_setting_card.setEnabled(available)
        if available:
            self.mps_setting_card.setVisible(True)
            self.mps_setting_card.setEnabled(True)
        else:
            self.mps_setting_card.setEnabled(False)
            self.mps_setting_card.setChecked(False)
        
    @pyqtSlot(object)
    def _on_memory_size_checked(self, memory_size_gb):
        """
        记录当前系统的内存或者显存（单位 GB）
        """
        self.memory_size_gb = memory_size_gb
    
    @pyqtSlot(bool)
    def _on_cuda_setting_changed(self, checked: bool):
        """
        关闭 cuda 会造成严重的性能下降。我们向用户二次确认是否要关闭。
        """
        if not checked:
            box = MessageBox(
                self.tr("确认关闭 GPU 推理吗？"),
                self.tr("关闭 GPU 推理可能导致性能显著下降。仍然要关闭吗？"),
                self.window()
            )
            box.yesButton.setText(self.tr("关闭"))
            box.cancelButton.setText(self.tr("取消"))
            if not box.exec():
                self.cuda_setting_card.setChecked(True)

    @pyqtSlot(bool)
    def _on_mps_setting_changed(self, checked: bool):
        """
        mps 在有的 Mac 设备上表现并不好。我们向用户二次确认是否要开启。
        """
        if checked:
            box = MessageBox(
                self.tr("确认启用 GPU 推理吗？"),
                self.tr("在某些 Mac 上，启用 GPU 推理将占用更多内存，且没有明显的性能和质量提升。仍然要启用吗？"),
                self.window()
            )
            box.yesButton.setText(self.tr("启用"))
            box.cancelButton.setText(self.tr("取消"))
            if not box.exec():
                self.mps_setting_card.setChecked(False)
    
    @pyqtSlot(int)
    def _on_max_model_count_changed(self, value: int):
        self.warning_timer.stop()
        if self.active_teaching_tip:
            try:
                self.active_teaching_tip.close()
            except RuntimeError:
                pass
            self.active_teaching_tip = None

        if self.memory_size_gb is not None:
            system = platform.system()
            if system == "Windows" or system == "Linux":
                too_much_model = self.memory_size_gb - 3 * value <= 3  # 粗略估计每个模型占用 3GB 显存，至少要保证剩余 3GB 显存
            elif system == "Darwin":
                too_much_model = self.memory_size_gb - 3 * value - 1 <= 4  # 模型占用内存 ~= 4+3*(value-1)GB，至少要保证剩余 4GB 内存
            else:
                too_much_model = False  # 无法判断的系统，默认不提示
                
            if too_much_model:
                self.warning_timer.start(500)

    @pyqtSlot()
    def _show_memory_warning(self):
        if self.active_teaching_tip:
            self.active_teaching_tip.close()
        
        subject = "显存" if platform.system() in ["Windows", "Linux"] else "内存"

        self.active_teaching_tip = TeachingTip.create(
            target=self.max_model_count_card.slider,
            icon=FluentIcon.INFO,
            title="",
            content=self.tr(f"当前设定值已超出系统推荐容量，请确保你的{subject}足够。"),
            isClosable=True,
            tailPosition=TeachingTipTailPosition.BOTTOM,
            duration=-1,
            parent=self.window()
        )


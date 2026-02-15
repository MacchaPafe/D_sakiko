from PyQt5.QtGui import QIcon, QFontDatabase, QFont, QPixmap
from PyQt5.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QStackedWidget, QWidget, QListWidget, QGroupBox, QGridLayout,
                             QButtonGroup, QToolButton, QDesktopWidget, QListWidgetItem, QProgressBar)
from PyQt5.QtCore import Qt, QSize, QObject, pyqtSignal, QRunnable, QThread, QThreadPool
from pathlib import Path
import os,sys,shutil,re

import platformdirs

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)
import ui_constants
from character import PrintInfo

current_config=ui_constants.CurrentConfig()

# ==========================================
# 页面 1：下载模式选择
# ==========================================
class PageModeSelect(QWidget):
    def __init__(self, parent_controller):
        super().__init__()
        self.controller = parent_controller


        layout = QVBoxLayout(self)
        btn_group=QGroupBox("选择下载模式")
        btn_mode1 = QPushButton("为软件包内角色添加服装")
        btn_mode2 = QPushButton("为新角色下载服装")
        btn_group_layout=QVBoxLayout()
        btn_group_layout.addWidget(btn_mode1)
        btn_group_layout.addWidget(btn_mode2)
        btn_group.setLayout(btn_group_layout)
        layout.addWidget(btn_group)
        btn_mode1.clicked.connect(lambda _: self.go_to_next_page(True))
        btn_mode2.clicked.connect(lambda _: self.go_to_next_page(False))

    def go_to_next_page(self,val:bool):
        current_config.download_for_existing_char=val
        #current_config()
        self.controller.go_to_character_select()


# ==========================================
# 页面 2：角色选择
# ==========================================
class PageCharSelect(QWidget):
    def __init__(self, parent_controller):
        super().__init__()
        self.controller = parent_controller
        self.character_list = parent_controller.character_list
        self.screen=QDesktopWidget().screenGeometry()
        self.resize(int(self.screen.width() * 0.3), int(self.screen.height() * 0.7))
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        # ==========================================
        # 上半部分：软件已有角色 (Existing Characters)
        # ==========================================
        self.top_group_box = QGroupBox("选择软件包内的角色")
        top_layout = QGridLayout(self.top_group_box)
        # 【关键工具】按钮组，确保互斥
        self.top_btn_group = QButtonGroup(self)
        # 连接信号：当组内任意按钮被点击，触发 handle_top_click
        self.top_btn_group.buttonClicked[int].connect(self.on_top_group_clicked)
        # 遍历 character_list 动态生成按钮
        for i, char_obj in enumerate(self.character_list):
            btn = QPushButton(char_obj.character_name)
            btn.setCheckable(True)
            btn.setStyleSheet('''
                QPushButton:checked {
                    background-color: #87CEFA; /* 选中时的背景色 */
                    color: #FFFFFF;
                }
            ''')
            # 加入布局 (每行放3个)
            top_layout.addWidget(btn, i // 3, i % 3)
            # 加入互斥组，并指定 ID 为列表索引，方便后续查找
            self.top_btn_group.addButton(btn, i)
        if current_config.download_for_existing_char:
            main_layout.addWidget(self.top_group_box)
        # ==========================================
        # 下半部分：BanG Dream 角色池 (Target Characters)
        # ==========================================
        self.bottom_group_box = QGroupBox("目标 BangDream 角色")
        bottom_layout = QGridLayout()
        bottom_layout.setHorizontalSpacing(int(self.screen.height() * 0.04 * 0.25))
        bottom_layout.setVerticalSpacing(int(self.screen.height() * 0.05 * 0.25))
        self.bottom_btn_group = QButtonGroup(self)
        self.bottom_btn_group.buttonClicked.connect(self.on_bottom_group_clicked)
        self.all_bottom_btns = []  # 存个引用，方便后面批量禁用/启用
        for i, (char_name,info) in enumerate(ui_constants.char_info_json.items()):
            btn = QToolButton()
            btn.setCheckable(True)
            btn.setEnabled(False)  # 【核心逻辑】初始化时全部不可用
            btn.setText(char_name)
            btn.setFixedSize(int(self.screen.height() * 0.06), int(self.screen.height() * 0.075))
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            if os.path.exists(f'./char_headprof/{char_name}.png'):
                btn.setIcon(QIcon(f'./char_headprof/{char_name}.png'))
                btn.setIconSize(
                    QSize(int(self.screen.height() * 0.06 * 0.7), int(self.screen.height() * 0.075 * 0.7)))
            btn.setStyleSheet(f"""
                    QToolButton {{
                        background-color: {info["theme_color"]}; 
                        color: #FFFFFF;  /* 强制文字白色，防止和背景混色 */
                        font-weight: bold;
                        border-radius: 6px; /* 加点圆角更好看 */
                    }}
                    QToolButton:hover {{
                        border: 2px solid white; /* 悬停加个边框 */
                    }}
                    QToolButton:checked {{
                        border: 4px solid #FFD700; /* 选中时加个金色边框 */
                    }}
                """)
            bottom_layout.addWidget(btn, i // 10, i % 10)  # 每行放10个
            self.bottom_btn_group.addButton(btn, i)
            self.all_bottom_btns.append(btn)
        self.bottom_group_box.setLayout(bottom_layout)
        main_layout.addWidget(self.bottom_group_box)
        # ==========================================
        # 底部导航栏
        # ==========================================
        nav_layout = QHBoxLayout()
        self.btn_prev = QPushButton("上一步")
        self.btn_prev.clicked.connect(self.controller.go_back_to_mode_select)
        self.btn_next = QPushButton("下一步")
        self.btn_next.setEnabled(False)  # 【核心逻辑】初始化不可用
        self.btn_next.clicked.connect(self.on_next_clicked)
        nav_layout.addWidget(self.btn_prev)
        nav_layout.addWidget(self.btn_next)

        main_layout.addLayout(nav_layout)

    # ==========================================
    # 逻辑处理函数
    # ==========================================

    def convert_display(self):
        self.top_group_box.setVisible(current_config.download_for_existing_char)
        if self.top_btn_group.checkedButton():
            self.top_btn_group.setExclusive(False)  # 临时取消互斥才能取消选中
            self.top_btn_group.checkedButton().setChecked(False)
            self.top_btn_group.setExclusive(True)
            # 清空下半部分选中
        if self.bottom_btn_group.checkedButton():
            self.bottom_btn_group.setExclusive(False)
            self.bottom_btn_group.checkedButton().setChecked(False)
            self.bottom_btn_group.setExclusive(True)
            # 处理下半部分的初始锁定状态
        if current_config.download_for_existing_char:
            # 如果需要选上面的，下面的先禁用
            for btn in self.all_bottom_btns:
                btn.setEnabled(False)
            self.bottom_group_box.setTitle("目标 BangDream 角色")
        else:
            # 如果上面隐藏了，下面直接启用
            for btn in self.all_bottom_btns:
                btn.setEnabled(True)
            self.bottom_group_box.setTitle("目标 BangDream 角色")
            # 重新检查下一步按钮 (此时应该变灰)
        self.check_next_button_state()

    def on_top_group_clicked(self, btn_id):
        """当上面的按钮被选中时触发"""
        current_config.selected_existing_character = self.character_list[btn_id].character_folder_name
        current_config.selected_existing_character_name = self.character_list[btn_id].character_name
        #current_config()
        # 解锁下半部分的所有按钮
        for btn in self.all_bottom_btns:
            btn.setEnabled(True)
        # 检查是否可以启用下一步 (可能用户是回头修改上面的，下面已经选过了)
        self.check_next_button_state()

    def on_bottom_group_clicked(self, btn):
        """当下面的按钮被选中时触发"""
        current_config.bestdori_chara_index = ui_constants.char_info_json[btn.text()]["bestdori_index"]
        current_config.bestdori_char_name = btn.text()
        #current_config()

        # 检查是否可以启用下一步
        self.check_next_button_state()

    def check_next_button_state(self):
        """何时能开启下一步"""
        top_selected = (self.top_btn_group.checkedButton() is not None) if current_config.download_for_existing_char else True
        bottom_selected = self.bottom_btn_group.checkedButton() is not None
        if top_selected and bottom_selected:
            self.btn_next.setEnabled(True)
        else:
            self.btn_next.setEnabled(False)

    def on_next_clicked(self):
        """点击下一步时的处理"""
        self.controller.go_to_costume_select()

from live2d_download.models import FileProgress, ModelProgress, ProgressCallback
from live2d_download.bestdori_client import BestdoriClient
from live2d_download.live2d_downloader import Live2dDownloader
from live2d_download.live2d_service import Live2dService
from live2d_download.models import CancelToken, CancelledError
# 创建客户端
client = BestdoriClient()
# 下载器自身需要一个客户端作为参数传入；客户端用于实际发起网络请求。
live2d_downloader = Live2dDownloader(client)
live2d_service = Live2dService(client)

# =================================================================================
# 页面 3：下载界面
# =================================================================================
# ==========================================
# 组件 1：单行列表项 UI (CostumeItemWidget)
# ==========================================
class CostumeItemWidget(QWidget):
    """
    角色服装的界面展示模板：
    """
    def __init__(self, costume_id, parent=None):
        super().__init__(parent)
        self.costume_id = costume_id
        # 布局初始化
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        screen=QDesktopWidget().screenGeometry()
        # 1. 左侧图标 (默认显示占位符或加载中)
        self.lbl_icon = QLabel()
        self.lbl_icon.setFixedSize(int(screen.height() * 0.11), int(screen.height() * 0.11))
        self.lbl_icon.setStyleSheet("background-color: #eee; border-radius: 5px;")
        self.lbl_icon.setAlignment(Qt.AlignCenter)
        self.lbl_icon.setPixmap(QPixmap("./icons/loading.png"))
        self.lbl_icon.setScaledContents(True)
        # 2. 中间文本区域
        text_layout = QVBoxLayout()
        self.lbl_name = QLabel("加载中...")  # 正式名称
        self.lbl_name.setStyleSheet(f"font-size: {int(screen.height() * 0.15*0.2)}; font-weight: bold;")
        self.lbl_id = QLabel(f"服装ID: {costume_id}")  # 内部 ID
        self.lbl_id.setStyleSheet(f"color: #888; font-size: {int(screen.height() * 0.15*0.07)};")
        text_layout.addStretch()
        text_layout.addWidget(self.lbl_name)
        text_layout.addWidget(self.lbl_id)
        text_layout.addStretch()
        # --- 3. 进度条区域 (封装在一个 Widget 里) ---
        # 建立一个容器 Widget，方便统一控制显隐
        self.progress_container = QWidget()
        self.progress_container.setStyleSheet("background-color: transparent;")
        progress_layout = QVBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)  # 内部无边距
        progress_layout.setSpacing(2)
        progress_layout.setAlignment(Qt.AlignVCenter)  # 垂直居中
        # A. 状态文字行 (水平布局：左边显示"下载中"，右边显示"5/20")
        status_line_layout = QHBoxLayout()
        self.lbl_progress_status = QLabel("下载中...")
        self.lbl_progress_status.setStyleSheet(f"color: #0078d7; font-size: {int(screen.height() * 0.11*0.13)}px;")
        self.lbl_progress_text = QLabel("0%")
        self.lbl_progress_text.setStyleSheet(f"color: #555; font-size: {int(screen.height() * 0.11*0.13)}px;")
        self.lbl_progress_text.setAlignment(Qt.AlignRight)
        status_line_layout.addWidget(self.lbl_progress_status)
        status_line_layout.addStretch()  # 把两段文字撑开
        status_line_layout.addWidget(self.lbl_progress_text)
        # B. 实体进度条 (QProgressBar)
        self.p_bar = QProgressBar()
        self.p_bar.setRange(0, 100)  # 0 到 100%
        self.p_bar.setValue(0)
        self.p_bar.setTextVisible(False)  # 不显示进度条内部的百分比文字，因为外面有了
        self.p_bar.setFixedHeight(6)  # 设为细条，比较精致
        self.p_bar.setFixedWidth(int(screen.width() * 0.1))  # 限制一下宽度，不要太长
        # 使用 QSS 美化进度条：圆角、蓝色背景
        self.p_bar.setStyleSheet("""
                    QProgressBar {
                        border: none;
                        background-color: #e0e0e0;
                        border-radius: 3px;
                    }
                    QProgressBar::chunk {
                        background-color: #0078d7; 
                        border-radius: 3px;
                    }
                """)

        progress_layout.addLayout(status_line_layout)
        progress_layout.addWidget(self.p_bar)

        # 默认隐藏进度区域
        self.progress_container.setVisible(False)
        # 3. 右侧下载按钮
        self.btn_download = QToolButton()
        self.btn_download.setIcon(QIcon("./icons/download.svg"))
        self.btn_download.setIconSize(QSize(int(screen.height() * 0.025), int(screen.height() * 0.025)))
        self.btn_download.setEnabled(False) #初始没有更新名称时不可点击
        self.btn_download.clicked.connect(self.on_download_btn_click)
        # 添加到主布局
        layout.addWidget(self.lbl_icon)
        layout.addLayout(text_layout)
        layout.addStretch()  # 弹簧，把按钮顶到最右边
        layout.addWidget(self.progress_container)
        layout.addSpacing(15)
        layout.addWidget(self.btn_download)
        layout.addSpacing(20)

        self.download_status="not_started"  # 下载状态：not_started / in_progress / succeeded，控制按钮的图标和行为

    def is_current_costume_already_existed(self,name):
        if not current_config.download_for_existing_char:
            return False
        all_costumes_folder_path = f"../live2d_related/{current_config.selected_existing_character}/extra_model"
        if not os.path.exists(all_costumes_folder_path):
            return False
        existing_costumes = os.listdir(all_costumes_folder_path)
        for costume_name in existing_costumes:
            if costume_name==name:
                return True

    def update_data(self, name, icon_data):
        """当数据下载完成后，调用此方法刷新界面"""
        name=name.strip() if name else name
        if name:
            if name!="Unknown":
                self.lbl_name.setText(name)
            else:
                if "casual" in self.costume_id:
                    extra_text_match=re.search(r"casual(.*)",self.costume_id)
                    self.lbl_name.setText("常服"+(extra_text_match.group(1) if extra_text_match else ""))
                elif "school_summer" in self.costume_id:
                    extra_text_match = re.search(r"school_summer(.*)", self.costume_id)
                    self.lbl_name.setText("夏季校服"+(extra_text_match.group(1) if extra_text_match else ""))
                elif "school_winter" in self.costume_id:
                    extra_text_match = re.search(r"school_winter(.*)", self.costume_id)
                    self.lbl_name.setText("冬季校服"+(extra_text_match.group(1) if extra_text_match else ""))
                else:
                    self.lbl_name.setText("未定名称")
        else:
            self.lbl_name.setText("未知服装")

        if icon_data:
            pixmap = QPixmap()
            if pixmap.loadFromData(icon_data):
                self.lbl_icon.setPixmap(pixmap)
                self.lbl_icon.setText("")  # 清除占位文字
            else:
                self.lbl_icon.setText("No Img")

        if self.is_current_costume_already_existed(name):
            icon = QIcon()
            icon.addPixmap(QPixmap("./icons/success.svg"), QIcon.Disabled, QIcon.Off)  # 解决disable后按钮图标为灰色
            self.btn_download.setIcon(icon)
            self.btn_download.setEnabled(False)
            self.download_status="succeeded"
        else:
            self.btn_download.setEnabled(True)

    def on_download_btn_click(self):
        if self.download_status=="not_started":
            PrintInfo.print_info(f"开始下载服装: {self.costume_id}")
            self.download_status="in_progress"
            self.btn_download.setIcon(QIcon("./icons/cancel.svg"))
            self.progress_container.setVisible(True)
            self.cancel_token = CancelToken.new()
            self.downloader=DownloadCostumeTask(self.costume_id,self.cancel_token)
            self.downloader.completed_signal.connect(self.download_succeeded)
            self.downloader.failed_signal.connect(self.download_failed)
            self.downloader.progress_signal.connect(self.update_progress)
            self.downloader.start()

        elif self.download_status=="in_progress":
            self.btn_download.setEnabled(False) #防止停止过程多次点击
            self.cancel_token.cancel()
            self.lbl_progress_status.setText("取消中")

    def update_progress(self,model:ModelProgress):
        """更新进度条显示"""
        # 更新状态文字
        self.lbl_progress_status.setText(f"下载中...")
        # 更新进度条数值
        if model.files_total > 0:
            progress_percent = int((model.files_done / model.files_total) * 100)
            self.p_bar.setValue(progress_percent)
            self.lbl_progress_text.setText(f"{(model.files_done / model.files_total):.0%}")
        else:
            self.p_bar.setValue(0)

    def download_succeeded(self):
        self.lbl_progress_status.setText("下载完成，安装文件中...")
        self.download_status="succeeded"
        icon=QIcon()
        icon.addPixmap(QPixmap("./icons/success.svg"), QIcon.Disabled, QIcon.Off)   #解决disable后按钮图标为灰色
        self.btn_download.setIcon(icon)
        self.btn_download.setEnabled(False)

        try:
            if current_config.download_for_existing_char:
                ui_constants.AddCostume.add_costume_for_existed_char(current_config.selected_existing_character,
                                                                     self.costume_id,
                                                                     self.lbl_name.text() if not self.lbl_name.text() in ["未知服装","未定名称"] else self.costume_id,)
            else:
                ui_constants.AddCostume.add_costume_for_new_character(current_config.bestdori_char_name,
                                                                      ui_constants.char_info_json[current_config.bestdori_char_name]["romaji"],
                                                                      self.costume_id)

            self.lbl_progress_status.setText("服装添加完成！")
        except Exception as e:
            PrintInfo.print_error(f"[Error]安装服装：{self.costume_id}失败，错误信息: {e}")
            self.lbl_progress_status.setText("安装文件过程出现出错，重下一遍试试")
            self.btn_download.setIcon(QIcon("./icons/download.svg"))
            self.btn_download.setEnabled(True)
            self.download_status="not_started"

    def download_failed(self,error_msg:str):
        if error_msg=="下载已取消":
            self.lbl_progress_status.setText("下载已取消")
            self.p_bar.setValue(0)
            self.lbl_progress_text.setText("0%")
        else:
            self.lbl_progress_status.setText("下载过程出现错误")
        self.download_status="not_started"
        self.btn_download.setIcon(QIcon("./icons/download.svg"))
        self.btn_download.setEnabled(True)
        try:
            cache_path = [Path("./.model_download_cache") / self.costume_id,Path(platformdirs.user_cache_path("D_sakiko"))/"live2d"/"assets"/"jp"/"live2d"/"chara"/self.costume_id]
            for _cache_path in cache_path:
                if _cache_path.exists() and _cache_path.is_dir():
                    shutil.rmtree(_cache_path)
        except Exception as e:
            PrintInfo.print_error(f"[Error]删除缓存文件失败，错误信息:{e}")
# ==========================================
# 下载模块
# ==========================================
class ProgressUpdater(ProgressCallback):
    def __init__(self, signal: pyqtSignal):
        self.signal = signal

    def __call__(self, *, file: FileProgress = None, model: ModelProgress = None):
        if file is not None:
            print(
                f"下载文件：{file.live2d_name}的{file.rel_path}，状态[{file.event}]，进度{file.bytes_done}/{file.bytes_total}")
        if model is not None:
            self.signal.emit(model)

class DownloadCostumeTask(QThread):
    completed_signal = pyqtSignal()  # 下载完成信号
    failed_signal = pyqtSignal(str)  # 下载失败信号
    progress_signal=pyqtSignal(object)
    def __init__(self,live2d_name:str,cancel_token:CancelToken):
        super().__init__()

        self.live2d_name=live2d_name
        self.cancel_token=cancel_token

    def run(self):
        progress_updater=ProgressUpdater(self.progress_signal)
        try:
            live2d_downloader.download_live2d_name(
                # live2d 服装的标识名称
                live2d_name=self.live2d_name,
                # 下载到哪个文件夹
                # 下载后，实际的文件存放位置为 models/036_live_event_307_ssr/
                root_dir=Path("./.model_download_cache"),
                # 可以选择使用或不使用进度回调
                progress=progress_updater,
                cancel=self.cancel_token
            )
            self.completed_signal.emit()
        except CancelledError:
            PrintInfo.print_info(f"取消下载服装： {self.live2d_name} ")
            self.failed_signal.emit('下载已取消')
        except Exception as e:
            PrintInfo.print_error(f"[Error]下载服装 {self.live2d_name} 失败: {e}")
            self.failed_signal.emit(str(e))


# ==========================================
# 组件 2：后台任务信号 (WorkerSignals)
# ==========================================
class WorkerSignals(QObject):
    """
    由于 QRunnable 不是 QObject，不能直接发信号，
    所以需要一个辅助类来定义信号。
    """
    # 成功信号：(costume_id, name, icon_bytes)
    data_ready = pyqtSignal(str, str, bytes)
    # 列表获取成功信号: (list)
    list_ready = pyqtSignal(list)


# ==========================================
# 组件 3：单个服装详情获取任务 (FetchMetaTask)
# ==========================================
class FetchMetaTask(QRunnable): #QRunnable 不同于 QThread，它不是一个线程类，只是定义了一个任务逻辑，配合QThreadPool进行多个小任务的并发执行。另外，不能直接发信号
    """
    线程池任务：获取单个服装的名称和图标
    """

    def __init__(self, service, costume_id):
        super().__init__()
        self.service = service
        self.costume_id = costume_id
        self.signals = WorkerSignals()

    def run(self):
        try:
            # 1. 获取名称
            name = self.service.get_costume_name(self.costume_id, other_language=True)
            # 2. 获取图标
            icon_bytes = self.service.get_costume_icon(self.costume_id)
            # 发射结果
            self.signals.data_ready.emit(self.costume_id, name if name else "Unknown", icon_bytes or b"")

        except Exception as e:
            PrintInfo.print_error(f"[Error]获取 {self.costume_id} 服装的名称与图标时出现错误: {e}")
            # 出错也可以发射一个空数据，防止界面一直转圈
            self.signals.data_ready.emit(self.costume_id, "获取失败", b"")


# ==========================================
# 组件 4：列表获取线程 (FetchListThread)
# ==========================================
class FetchListThread(QThread):
    """
    专门用于第一步：获取所有服装 ID 列表
    """
    list_ready = pyqtSignal(list)

    def __init__(self, service, char_id):
        super().__init__()
        self.service = service
        self.char_id = char_id

    def run(self):
        try:
            # 调用 search_costumes (注意是复数，根据 API 文档)
            costumes = self.service.search_costumes(self.char_id)
            self.list_ready.emit(costumes)
        except Exception as e:
            PrintInfo.print_error(f"[Error]获取服装列表失败，错误信息: {e}")
            self.list_ready.emit([])


# ==========================================
# 主界面：PageCostumeDownload
# ==========================================
class PageCostumeDownload(QDialog):
    def __init__(self, parent_window):
        super().__init__()
        self.parent_window = parent_window
        self.char_id = current_config.bestdori_chara_index  # 当前选中的角色 ID
        self.setWindowTitle("3/3：选择服装下载")
        screen=QDesktopWidget().screenGeometry()
        self.resize(int(screen.width() * 0.4), int(screen.height() * 0.7))

        # 线程池
        self.thread_pool = QThreadPool()
        # 限制并发数，防止瞬间发起几十个 HTTP 请求导致网络拥塞或被 Ban
        self.thread_pool.setMaxThreadCount(8)
        self.setStyleSheet(ui_constants.dialogWindowDefaultCss)

        self.init_ui()
        # 界面初始化后，自动开始加载数据
        self.start_loading_list()

    def init_ui(self):
        layout = QVBoxLayout(self)
        # 1. 顶部提示
        self.lbl_title_0= QLabel(f"为角色 {current_config.selected_existing_character_name} 准备新服装")
        self.lbl_title = QLabel(f"正在加载BangDream角色 {current_config.bestdori_char_name} 的服装列表...")
        if current_config.download_for_existing_char:
            layout.addWidget(self.lbl_title_0)
        layout.addWidget(self.lbl_title)
        # 2. 列表区域 (使用 QListWidget)
        self.list_widget = QListWidget()
        # 设置样式：让每个 item 之间有点间距
        self.list_widget.setStyleSheet("QListWidget::item { border-bottom: 1px solid #ddd; }")
        layout.addWidget(self.list_widget)
        # 3. 底部返回按钮
        btn_back = QPushButton("返回选择角色")
        btn_back.clicked.connect(self.go_back)
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_back)
        layout.addLayout(btn_layout)

    # --- 第一步：加载所有服装列表 ---
    def start_loading_list(self):
        self.list_thread = FetchListThread(live2d_service, self.char_id)
        self.list_thread.list_ready.connect(self.on_list_ready)
        self.list_thread.start()

    def on_list_ready(self, costumes):
        """列表 ID 下载好了，开始生成 UI 骨架"""
        if not costumes:
            self.lbl_title.setText("加载服装数据失败，请检查网络连接。")
            return
        self.lbl_title.setText(f"找到 {current_config.bestdori_char_name} 的 {len(costumes)} 套服装")

        # 保存一个字典，方便后续根据 ID 找到对应的 Widget
        self.item_map = {}
        # 生成空壳 UI
        for costume_id in costumes:
            # 1. 创建 QListWidgetItem
            item = QListWidgetItem(self.list_widget)
            # 2. 创建自定义 Widget
            widget = CostumeItemWidget(costume_id)
            item.setSizeHint(widget.sizeHint())  # 确保 Item 高度适应 Widget
            # 3. 关联
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
            self.item_map[costume_id] = widget
            # 4. 【关键】立刻把这个 Item 的详细信息抓取任务扔进线程池
            self.spawn_meta_task(costume_id)

    # --- 第二步：并发加载详情 ---
    def spawn_meta_task(self, costume_id):
        task = FetchMetaTask(live2d_service, costume_id)
        # 连接信号：当数据准备好时，更新 UI
        task.signals.data_ready.connect(self.update_item_ui)
        self.thread_pool.start(task)

    def update_item_ui(self, costume_id, name, icon_bytes):
        """子线程回来汇报数据了，更新对应的 Widget"""
        if costume_id in self.item_map:
            widget = self.item_map[costume_id]
            widget.update_data(name, icon_bytes)

    def go_back(self):
        # 退出前最好清理一下线程池（可选）
        self.thread_pool.clear()
        if self.parent_window:
            self.parent_window.setVisible(True)
        self.close()


# ==========================================
# 主窗口控制器（核心逻辑）
# ==========================================
class DownloadWizardWindow(QDialog):
    def __init__(self,characters):
        super().__init__()
        self.setWindowTitle("Live2D模型下载器")
        self.character_list=characters
        # 创建堆叠窗口
        self.stack = QStackedWidget()
        # 3. 初始化页面
        self.page1 = PageModeSelect(self)
        self.page2 = PageCharSelect(self)
        # 4. 按顺序加入堆叠
        self.stack.addWidget(self.page1)  # index 0
        self.stack.addWidget(self.page2)  # index 1
        # 5. 布局设置
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self.stack)
        self.setStyleSheet(ui_constants.dialogWindowDefaultCss)


    # --- 跳转逻辑 ---
    def go_to_character_select(self):
        """跳转到第2页"""
        self.setWindowTitle("2/3：这位角色是？")
        self.page2.convert_display()
        self.stack.setCurrentIndex(1)

    def go_to_costume_select(self):
        """跳转到第3页"""
        self.download_page= PageCostumeDownload(self)
        self.setVisible(False)
        self.download_page.exec_()


    def go_back_to_mode_select(self):
        """返回第一页"""
        self.setWindowTitle("1/3：选择下载模式")
        self.stack.setCurrentIndex(0)


# ==========================================
# 运行测试
# ==========================================
if __name__ == '__main__':
    import os, sys

    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    import character
    get_all = character.GetCharacterAttributes()
    app = QApplication(sys.argv)

    font_path = '../font/msyh.ttc'
    font_id = QFontDatabase.addApplicationFont(font_path)  # 设置字体
    font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
    font = QFont(font_family, 12)
    app.setFont(font)
    window = DownloadWizardWindow(get_all.character_class_list)
    window.show()
    app.exec_()

    if os.path.exists("./.model_download_cache"):
        shutil.rmtree("./.model_download_cache")


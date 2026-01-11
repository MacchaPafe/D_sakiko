import sys,json,os,shutil,glob,time
from PyQt5.QtWidgets import (QApplication, QWidget, QRadioButton,
                             QVBoxLayout, QLabel, QButtonGroup, QHBoxLayout, QLineEdit, QPushButton, QListWidget,
                             QAbstractItemView, QSizePolicy, QFileDialog, QDesktopWidget)


script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

import character

def migrate_from_old_config():
    """
    将旧版的分散配置文件迁移到新的统一配置文件中，并且删除旧的配置文件。
    """
    old_png_file=["./microphone.png",
                  "./exit.png",
                  "./setting.png",
                  "./music.png",
                  "./star.png",
                  "./save.png"]
    for png_file in old_png_file:
        if os.path.exists(png_file):
            try:
                os.remove(png_file)
            except Exception as e:
                print(f"[Warning]无法删除旧的图标文件 {png_file} 。该错误不影响正常使用，可尝试手动删除该文件。错误信息：{e}")
                pass

    old_file_names=[
        "../is_fp32.txt",
        "../API Key.txt",
        "../API_Choice.json",
        "../if_delete_audio_cache.txt",
        "../reference_audio/GSV_sample_rate.txt",
        "../reference_audio/character_order.json"
    ]
    flag=False
    for file in old_file_names:
        if os.path.exists(file):
            flag=True   #有旧配置文件就重新设置
    if not flag:
        return  #没有旧配置文件，直接返回

    if os.path.exists("../dsakiko_config.json"):
        for file in old_file_names:
            try:
                os.remove(file)
            except Exception:
                pass
        return
    print("检测到旧版配置文件，正在迁移到新版配置文件...")
    new_config_template={"character_setting":{
                            "character_order": {
                                "character_num": 3,
                                "character_names": [
                                    "爱音",
                                    "祥子",
                                    "素世"
                                ]
                            }
                         },
                         "llm_setting": {
                             "is_deepseek": True,
                             "deepseek_key": 'use_api_of_up',
                             "other_provider":[
                                 {"name": "OpenAI",
                                  "if_choose": False,
                                  "api_key": "sk-24xxx",
                                  "base_url": "https://api.openai.com/v1",
                                  "model": "gpt-4"
                                  },
                                 {"name": "Google",
                                  "if_choose": False,
                                  "api_key": "....",
                                  "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                                  "model": "gemini-2.5-pro"
                                  },
                                 {"name": "ModelScope",
                                  "model": "deepseek-ai/DeepSeek-V3.2",
                                  "api_key": "....",
                                  "base_url": "https://api-inference.modelscope.cn/v1",
                                  "if_choose": False
                                  }
                             ]
                         },
                         "audio_setting":{
                            "enable_fp16_inference": False,
                            "if_delete_audio_cache":False,
                            "sovits_inference_sampling_steps":16
                         }
    }

    if os.path.exists('../API_Choice.json'):
        with open("../API_Choice.json", "r", encoding="utf-8") as f:
            llm_choice = json.load(f)
            llm_choice=llm_choice["llm_choose"]
        active_provider=None
        for provider in llm_choice:
            if provider["if_choose"]:
                active_provider = provider
        if active_provider is not None:
            new_config_template["llm_setting"]["is_deepseek"] = False
            if active_provider["name"]=="OpenAI":
                new_config_template["llm_setting"]["other_provider"][0]["if_choose"] = True
                new_config_template["llm_setting"]["other_provider"][0]["api_key"] = active_provider.get("api_key","")
                new_config_template["llm_setting"]["other_provider"][0]["model"] = active_provider.get("model","gpt-4")
            elif active_provider["name"]=="Google":
                new_config_template["llm_setting"]["other_provider"][1]["if_choose"] = True
                new_config_template["llm_setting"]["other_provider"][1]["api_key"] = active_provider.get("api_key","")
                new_config_template["llm_setting"]["other_provider"][1]["model"] = active_provider.get("model","gemini-2.5-pro")
        else:   # 没有选择任何非 DeepSeek 的 API，说明正使用deepseek
            new_config_template["llm_setting"]["is_deepseek"] = True
            if os.path.exists("../API Key.txt"):
                with open("../API Key.txt", "r", encoding="utf-8") as f:
                    api_key = f.read().strip()
                    if api_key:
                        new_config_template["llm_setting"]["deepseek_key"] = api_key
                    else:
                        new_config_template["llm_setting"]["deepseek_key"] = "use_api_of_up"

    if os.path.exists('../is_fp32.txt'):
        with open("../is_fp32.txt", "r", encoding="utf-8") as f:
            use_fp32_str = f.read().strip()
        try:
            new_config_template["audio_setting"]["enable_fp16_inference"] = not bool(int(use_fp32_str))
        except Exception:
            pass

    if os.path.exists('../if_delete_audio_cache.txt'):
        with open("../if_delete_audio_cache.txt", "r", encoding="utf-8") as f:
            delete_cache_str = f.read().strip()
        try:
            new_config_template["audio_setting"]["if_delete_audio_cache"] = bool(int(delete_cache_str))
        except Exception:
            pass

    if os.path.exists('../reference_audio/GSV_sample_rate.txt'):
        with open("../reference_audio/GSV_sample_rate.txt", "r", encoding="utf-8") as f:
            steps_str = f.read().strip()
        try:
            steps = int(steps_str)
            if steps in [4, 8, 16, 32]:
                new_config_template["audio_setting"]["sovits_inference_sampling_steps"] = steps
        except Exception:
            pass

    if os.path.exists("../reference_audio/character_order.json"):
        with open("../reference_audio/character_order.json", "r", encoding="utf-8") as f:
            character_order = json.load(f)
        try:
            new_config_template["character_setting"]["character_order"] = character_order
        except Exception:
            pass

    #保存新配置文件
    try:
        with open("../dsakiko_config.json", "w", encoding="utf-8") as f:
            json.dump(new_config_template, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"无法保存新的配置文件。请检查程序是否有写入权限。错误信息：{e}")
        return
    # 删除旧文件
    for file in old_file_names:
        try:
            os.remove(file)
        except Exception as e:
            print(f"[Warning]无法删除旧的配置文件 {file} 。请手动删除该文件。错误信息：{e}")




class conf_ui(QWidget):
    def __init__(self):
        super().__init__()
        with open("../dsakiko_config.json", "r", encoding="utf-8") as f:
            self.config = json.load(f)
        self.initUI()

    def initUI(self):
        self.setWindowTitle('数字小祥启动参数配置')

        layout = QVBoxLayout()

        label_api = QLabel('1.当前大模型API配置：')
        layout.addWidget(label_api)

        self.radio1 = QRadioButton('使用up的deepseek')
        self.radio2 = QRadioButton('你的deepseek API')
        self.radio3 = QRadioButton('OpenAI ChatGPT')
        self.radio4 = QRadioButton('Google Gemini')
        self.radio5= QRadioButton('魔搭社区 API')
        current_api_config=self.config["llm_setting"]
        self.llm_name = 'deepseek_up'
        if current_api_config["is_deepseek"]:
            self.llm_name='deepseek_up' if current_api_config["deepseek_key"]=='use_api_of_up' else 'deepseek_user'
        else:
            for provider in current_api_config["other_provider"]:
                if provider["if_choose"]:
                    self.llm_name=provider["name"]
                    self.other_api_key=provider["api_key"]
                    self.other_model_name=provider["model"]
                    break
        if self.llm_name=='deepseek_up':
            self.radio1.setChecked(True)
        elif self.llm_name=='deepseek_user':
            self.radio2.setChecked(True)
        elif self.llm_name=='OpenAI':
            self.radio3.setChecked(True)
        elif self.llm_name=='Google':
            self.radio4.setChecked(True)
        elif self.llm_name=='ModelScope':
            self.radio5.setChecked(True)

        self.api_buttonGroup = QButtonGroup()
        self.api_buttonGroup.addButton(self.radio1)
        self.api_buttonGroup.addButton(self.radio2)
        self.api_buttonGroup.addButton(self.radio3)
        self.api_buttonGroup.addButton(self.radio4)
        self.api_buttonGroup.addButton(self.radio5)
        self.api_buttonGroup.buttonClicked.connect(self.radio_button_clicked)

        self.api_button_layout=QHBoxLayout()
        self.api_button_layout.addWidget(self.radio1)
        self.api_button_layout.addWidget(self.radio2)
        self.api_button_layout.addWidget(self.radio3)
        self.api_button_layout.addWidget(self.radio4)
        self.api_button_layout.addWidget(self.radio5)
        layout.addLayout(self.api_button_layout)

        self.api_info_layout=QHBoxLayout()
        self.api_llm_model_label=QLabel('具体模型名称')
        self.api_llm_model_input=QLineEdit()
        self.api_llm_model_input.setToolTip('请严格按照官方给出的名称填写')
        self.api_key_label=QLabel('API Key')
        self.api_key_input=QLineEdit()
        self.api_info_layout.addWidget(self.api_llm_model_label)
        self.api_info_layout.addWidget(self.api_llm_model_input)
        self.api_info_layout.addWidget(self.api_key_label)
        self.api_info_layout.addWidget(self.api_key_input)
        layout.addLayout(self.api_info_layout)
        self.set_api_conf_value()

        label_2 = QLabel('2.退出程序后是否删除缓存音频：（鼠标悬浮查看说明）')
        label_2.setToolTip("删除历史生成音频可以节省硬盘空间，但如果没备份的话，点击历史消息就无法再播放对应历史音频！如果确定要删除，建议备份生成不错的那几句。")
        self.radio_2_1 = QRadioButton('不删除')
        self.radio_2_1.setToolTip("删除历史生成音频可以节省硬盘空间，但如果没备份的话，点击历史消息就无法再播放对应历史音频！如果确定要删除，建议备份生成不错的那几句。")
        self.radio_2_2 = QRadioButton('删除')
        self.radio_2_2.setToolTip("删除历史生成音频可以节省硬盘空间，但如果没备份的话，点击历史消息就无法再播放对应历史音频！如果确定要删除，建议备份生成不错的那几句。")
        self.btn_group_2= QButtonGroup()
        self.btn_group_2.addButton(self.radio_2_1)
        self.btn_group_2.addButton(self.radio_2_2)

        if self.config["audio_setting"]["if_delete_audio_cache"]:
            self.radio_2_2.setChecked(True)
        else:
            self.radio_2_1.setChecked(True)
        radio_2_layout=QHBoxLayout()
        radio_2_layout.addWidget(self.radio_2_1)
        radio_2_layout.addWidget(self.radio_2_2)
        layout.addWidget(label_2)
        layout.addLayout(radio_2_layout)

        label_3=QLabel('3.是否启用fp16（半精度浮点）推理音频：（鼠标悬浮查看说明）')
        label_3.setToolTip("启用后可以加快推理速度，但会小幅损失一些音质。注意！gtx16系（不包括rtx20系）以及之前的显卡不要开启！")
        self.radio_3_1 = QRadioButton('不启用')
        self.radio_3_1.setToolTip("启用后可以加快推理速度，但会小幅损失一些音质。注意！gtx16系（不包括rtx20系）以及之前的显卡不要开启！")
        self.radio_3_2 = QRadioButton('启用')
        self.radio_3_2.setToolTip("启用后可以加快推理速度，但会小幅损失一些音质。注意！gtx16系（不包括rtx20系）以及之前的显卡不要开启！")
        self.btn_group_3= QButtonGroup()
        self.btn_group_3.addButton(self.radio_3_1)
        self.btn_group_3.addButton(self.radio_3_2)

        if self.config["audio_setting"]["enable_fp16_inference"]:
            self.radio_3_2.setChecked(True)
        else:
            self.radio_3_1.setChecked(True)
        radio_3_layout=QHBoxLayout()
        radio_3_layout.addWidget(self.radio_3_1)
        radio_3_layout.addWidget(self.radio_3_2)
        layout.addWidget(label_3)
        layout.addLayout(radio_3_layout)

        label_4=QLabel('4.可设置GPT-SoVITS推理采样步数：（鼠标悬浮查看说明）')
        label_4.setToolTip("降低采样步数可降低生成时间，但生成质量也会降低；步数越高，音质越好，推理时间也会相应增加。建议根据自己的硬件性能和需求进行调整。默认是16。")    #共有四档，4、8、16、32
        self.radio_4_1 = QRadioButton('4')
        self.radio_4_1.setToolTip("降低采样步数可降低生成时间，但生成质量也会降低；步数越高，音质越好，推理时间也会相应增加。建议根据自己的硬件性能和需求进行调整。默认是16。")
        self.radio_4_2 = QRadioButton('8')
        self.radio_4_2.setToolTip("降低采样步数可降低生成时间，但生成质量也会降低；步数越高，音质越好，推理时间也会相应增加。建议根据自己的硬件性能和需求进行调整。默认是16。")
        self.radio_4_3 = QRadioButton('16')
        self.radio_4_3.setToolTip("降低采样步数可降低生成时间，但生成质量也会降低；步数越高，音质越好，推理时间也会相应增加。建议根据自己的硬件性能和需求进行调整。默认是16。")
        self.radio_4_4 = QRadioButton('32')
        self.radio_4_4.setToolTip("降低采样步数可降低生成时间，但生成质量也会降低；步数越高，音质越好，推理时间也会相应增加。建议根据自己的硬件性能和需求进行调整。默认是16。")
        self.btn_group_4= QButtonGroup()
        self.btn_group_4.addButton(self.radio_4_1)
        self.btn_group_4.addButton(self.radio_4_2)
        self.btn_group_4.addButton(self.radio_4_3)
        self.btn_group_4.addButton(self.radio_4_4)
        sampling_rate=int(self.config["audio_setting"]["sovits_inference_sampling_steps"])
        if sampling_rate==4:
            self.radio_4_1.setChecked(True)
        elif sampling_rate==8:
            self.radio_4_2.setChecked(True)
        elif sampling_rate==16:
            self.radio_4_3.setChecked(True)
        elif sampling_rate==32:
            self.radio_4_4.setChecked(True)
        else:
            self.radio_4_3.setChecked(True)
        radio_4_layout=QHBoxLayout()
        radio_4_layout.addWidget(self.radio_4_1)
        radio_4_layout.addWidget(self.radio_4_2)
        radio_4_layout.addWidget(self.radio_4_3)
        radio_4_layout.addWidget(self.radio_4_4)
        layout.addWidget(label_4)
        layout.addLayout(radio_4_layout)

        label_5=QLabel('5.调整角色登场顺序：（拖拽调整位置）')
        characters=character.GetCharacterAttributes()
        chatacter_list=characters.character_class_list
        self.character_names=[char.character_name for char in chatacter_list]
        self.character_list_widget=QListWidget()
        self.character_list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        self.character_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.character_list_widget.addItems(self.character_names)
        layout.addWidget(label_5)
        layout.addWidget(self.character_list_widget)

        fun_6_layout = QHBoxLayout()
        # 设置控件之间的间距，数字越小挨得越近
        fun_6_layout.setSpacing(10)
        label_6 = QLabel('6.可更改字体：')
        # 让标签也自适应大小，不抢空间
        label_6.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_fun_6 = QPushButton('选择字体文件')
        self.btn_fun_6.clicked.connect(self.user_select_font_file)
        # 【关键点1】设置按钮的大小策略为 Fixed
        # 意思就是：按钮的大小完全由它的内容（文字）决定，绝不拉伸
        self.btn_fun_6.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.label_6_info = QLabel('')
        fun_6_layout.addWidget(label_6)
        fun_6_layout.addWidget(self.btn_fun_6)
        fun_6_layout.addWidget(self.label_6_info)
        # 在最后添加一个弹簧
        # 这个弹簧会占据这一行所有剩下的空白区域，把前面三个控件挤到最左边
        fun_6_layout.addStretch(1)
        layout.addLayout(fun_6_layout)


        self.save_btn=QPushButton('保存配置')
        self.save_btn.clicked.connect(self.save_config)
        layout.addWidget(self.save_btn)
        self.save_success_label=QLabel('')

        self.exit_btn=QPushButton('关闭窗口')
        self.exit_btn.clicked.connect(self.close)
        layout.addWidget(self.exit_btn)

        layout.addWidget(self.save_success_label)

        self.setLayout(layout)
        self.setStyleSheet("""
                                    QWidget {
                                        background-color: #E6F2FF;
                                        color: #7799CC;
                                    }
                                    QLabel {                    
                                        font-weight: bold;
                                    }
                                    QTextBrowser{
                                        text-decoration: none;
                                        background-color: #FFFFFF;
                                        border: 3px solid #B3D1F2;
                                        border-radius:9px;
                                        padding: 5px;
                                    }

                                    QLineEdit {
                                        font-weight: bold;
                                        background-color: #FFFFFF;
                                        border: 2px solid #B3D1F2;
                                        border-radius: 9px;
                                        padding: 5px;
                                    }
                                    
                                    QRadioButton {
                                        font-weight: bold;
                                    }
                                    
                                    QPushButton {                
                                        font-weight: bold;
                                        background-color: #7FB2EB;
                                        color: #ffffff;
                                        border-radius: 6px;
                                        padding: 6px;
                                    }

                                    QPushButton:hover {
                                        background-color: #3FB2EB;
                                    }

                                    QScrollBar:vertical {
                                        border: none;
                                        background: #D0E2F0;
                                        width: 10px;
                                        margin: 0px 0px 0px 0px;
                                    }

                                    QScrollBar::handle:vertical {
                                        background: #B3D1F2;
                                        min-height: 20px;
                                        border-radius: 3px;
                                    }

                                    QSlider::groove:horizontal {
                                        /* 滑槽背景 */
                                        border: 1px solid #B3D1F2;  /* 使用边框色作为滑槽边框 */
                                        height: 8px;
                                        background: #D0E2F0;       /* 使用浅色背景 */
                                        margin: 2px 0;
                                        border-radius: 4px;
                                    }

                                    QSlider::handle:horizontal {
                                        /* 滑块手柄 */
                                        background: #7FB2EB;       /* 使用按钮的亮蓝色 */
                                        border: 1px solid #4F80E0;
                                        width: 16px;
                                        margin: -4px 0;            /* 垂直方向上的偏移，使手柄在滑槽上居中 */
                                        border-radius: 8px;        /* 使手柄成为圆形 */
                                    }

                                    QSlider::handle:horizontal:hover {
                                        /* 鼠标悬停时的手柄颜色 */
                                        background: #3FB2EB;       /* 使用按钮的 hover 亮色 */
                                        border: 1px solid #3F60D0;
                                    }

                                    QSlider::sub-page:horizontal {
                                        /* 进度条（已滑过部分） */
                                        background: #AACCFF;       /* 使用一个中间的蓝色，比滑槽背景深，比手柄浅 */
                                        border-radius: 4px;
                                        margin: 2px 0;
                                    }
                                    
                                    QListWidget {
                                        background-color: #FFFFFF;
                                        border: 3px solid #B3D1F2;  /* 3px 稍粗边框 */
                                        border-radius: 9px;         /* 9px 圆角 */
                                        padding: 5px;               /* 内边距，让文字不贴边 */
                                        outline: 0px;               /* 去除选中时的虚线框，更美观 */
                                        color: #7799CC;             /* 字体颜色 */
                                    }
                                
                                    /* 列表中的每一项 */
                                    QListWidget::item {
                                        height: 30px;               /* 给每一项固定的高度，方便拖拽 */
                                        padding-left: 10px;         /* 文字左侧留白 */
                                        border-radius: 5px;         /* 列表项内部也做小圆角 */
                                        margin-bottom: 2px;         /* 项与项之间留一点缝隙 */
                                    }
                                
                                    /* 鼠标悬停在项上时 */
                                    QListWidget::item:hover {
                                        background-color: #E6F2FF;  /* 非常浅的蓝色背景 */
                                    }
                                
                                    /* 选中某一项时 */
                                    QListWidget::item:selected {
                                        background-color: #7FB2EB;  /* 按钮同款深蓝色背景 */
                                        color: #FFFFFF;             /* 文字变白 */
                                    }
                                    
                                    /* 拖拽过程中的样式（可选） */
                                    QListWidget::item:selected:!active {
                                        background-color: #9FC5EE;  /* 当列表失去焦点但仍被选中时的颜色 */
                                    }

                                """)

    def set_api_conf_value(self):
        if self.llm_name in ['deepseek_up','deepseek_user']:
            self.api_llm_model_input.setText('deepseek V3.2')
            self.api_llm_model_input.setDisabled(True)
        else:
            self.api_llm_model_input.setDisabled(False)
            for provider in self.config['llm_setting']['other_provider']:
                if provider['name']==self.llm_name:
                    self.api_llm_model_input.setText(provider['model'])
                    break

        if self.llm_name =='deepseek_up':
            self.api_key_input.setText('')
            self.api_key_input.setDisabled(True)
        elif self.llm_name=='deepseek_user':
            self.api_key_input.setText(self.config['llm_setting']['deepseek_key'] if self.config['llm_setting']['deepseek_key']!='use_api_of_up' else '')
            self.api_key_input.setDisabled(False)
        else:
            self.api_key_input.setDisabled(False)
            for provider in self.config['llm_setting']['other_provider']:
                if provider['name']==self.llm_name:
                    self.api_key_input.setText(provider['api_key'])
                    break

    def radio_button_clicked(self,button):
        if button.text()=='使用up的deepseek':
            self.llm_name='deepseek_up'
            self.config['llm_setting']['is_deepseek']=True
            self.config['llm_setting']['deepseek_key']='use_api_of_up'
            for i in range(len(self.config['llm_setting']['other_provider'])):
                self.config['llm_setting']['other_provider'][i]['if_choose']=False
        elif button.text()=='你的deepseek API':
            self.llm_name='deepseek_user'
            self.config['llm_setting']['is_deepseek'] = True
            for i in range(len(self.config['llm_setting']['other_provider'])):
                self.config['llm_setting']['other_provider'][i]['if_choose'] = False
        else:
            if button.text()=='OpenAI ChatGPT':
                self.llm_name='OpenAI'
            elif button.text()=='Google Gemini':
                self.llm_name='Google'
            elif button.text()=='魔搭社区 API':
                self.llm_name='ModelScope'
            self.config['llm_setting']['is_deepseek'] = False
            for i in range(len(self.config['llm_setting']['other_provider'])):
                if self.config['llm_setting']['other_provider'][i]['name'] == self.llm_name:
                    self.config['llm_setting']['other_provider'][i]['if_choose'] = True
                else:
                    self.config['llm_setting']['other_provider'][i]['if_choose'] = False


        self.set_api_conf_value()
        self.save_success_label.setText('')

    def save_config(self):
        if self.llm_name=='deepseek_up':
            pass
        elif self.llm_name=='deepseek_user':
            self.config['llm_setting']['deepseek_key']=self.api_key_input.text().strip()
        else:
            for i in range(len(self.config['llm_setting']['other_provider'])):
                if self.config['llm_setting']['other_provider'][i]['name']==self.llm_name:
                    self.config['llm_setting']['other_provider'][i]['api_key']=self.api_key_input.text().strip()
                    self.config['llm_setting']['other_provider'][i]['model']=self.api_llm_model_input.text().strip()
                    break

        if self.radio_2_1.isChecked():
            self.config["audio_setting"]["if_delete_audio_cache"]=False
        else:
            self.config["audio_setting"]["if_delete_audio_cache"]=True

        if self.radio_3_1.isChecked():
            self.config["audio_setting"]["enable_fp16_inference"]=False
        else:
            self.config["audio_setting"]["enable_fp16_inference"]=True

        if self.radio_4_1.isChecked():
            sampling_steps=4
        elif self.radio_4_2.isChecked():
            sampling_steps=8
        elif self.radio_4_3.isChecked():
            sampling_steps=16
        elif self.radio_4_4.isChecked():
            sampling_steps=32
        self.config["audio_setting"]["sovits_inference_sampling_steps"]=sampling_steps

        ordered_names=[]
        count = self.character_list_widget.count()
        for i in range(count):
            item = self.character_list_widget.item(i)
            ordered_names.append(item.text())
        order_data_to_save={
            "character_num": len(ordered_names),
            "character_names": ordered_names,
        }
        self.config["character_setting"]["character_order"]=order_data_to_save
        with open("../dsakiko_config.json", "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

        self.save_success_label.setText('保存成功！下次启动将应用配置')

    def user_select_font_file(self):
        file_path, file_type = QFileDialog.getOpenFileName(
            self,
            "选择字体文件（.ttf/.otf/.ttc）",
            "",
            "字体类型文件 (*.ttf *.otf *.ttc)"
        )
        if not file_path:
            self.label_6_info.setText('取消了选择')
            return

        try:
            # 1. 生成带时间戳的唯一新文件名
            timestamp = int(time.time())
            file_ext = os.path.splitext(file_path)[1].lower()
            new_filename = f"custom_font_{timestamp}{file_ext}"
            dest_path = os.path.join('../font/', new_filename)

            # 2. 先尝试清理旧文件（尽力而为，删不掉也不报错）
            # 查找所有名字是 custom_font_ 开头的文件
            old_files = glob.glob(os.path.join('../font/', 'custom_font_*'))
            for old_file in old_files:
                try:
                    os.remove(old_file)
                    print(f"已清理旧文件: {old_file}")
                except Exception:
                    # 关键点：如果旧文件被锁，直接跳过，不要抛出异常打断流程
                    print(f"旧文件被占用，本次跳过删除: {old_file}")

            # 3. 复制新文件 (因为名字是唯一的，绝对不会冲突)
            shutil.copy(file_path, dest_path)
            self.label_6_info.setText('成功应用字体')

        except Exception as e:
            self.label_6_info.setText('字体应用失败')
            print('错误信息：', e)




if __name__ == '__main__':
    migrate_from_old_config()
    if not os.path.exists("../dsakiko_config.json"):
        raise FileNotFoundError("没有找到统一配置文件dsakiko_config.json，请重新运行本程序。如果还出现这条报错信息，请联系up。")
    app = QApplication(sys.argv)
    win = conf_ui()

    win.show()
    sys.exit(app.exec_())
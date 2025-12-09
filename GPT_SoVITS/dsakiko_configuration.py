import sys,json,os,shutil,glob,time
from PyQt5.QtWidgets import (QApplication, QWidget, QRadioButton,
                             QVBoxLayout, QLabel, QButtonGroup, QHBoxLayout, QLineEdit, QPushButton, QListWidget,
                             QAbstractItemView, QSizePolicy, QFileDialog)


script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

import character

class conf_ui(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowTitle('数字小祥启动参数配置')
        #self.setGeometry(100, 100, 300, 200)

        layout = QVBoxLayout()

        label_api = QLabel('1.当前大模型API配置：')
        layout.addWidget(label_api)

        self.radio1 = QRadioButton('使用up的deepseek')
        self.radio2 = QRadioButton('你的deepseek API')
        self.radio3 = QRadioButton('OpenAI ChatGPT')
        self.radio4 = QRadioButton('Google Gemini')
        with open('../API_Choice.json','r',encoding='utf-8') as f:
            self.current_api_conf=json.load(f)
        self.llm_name='deepseek_up'
        if os.path.getsize('../API Key.txt')!=0:
            self.llm_name='deepseek_user'
        for llm_choice in self.current_api_conf['llm_choose']:
            if llm_choice['if_choose']:
                self.llm_name=llm_choice['name']
                break

        if self.llm_name=='deepseek_up':
            self.radio1.setChecked(True)
        elif self.llm_name=='deepseek_user':
            self.radio2.setChecked(True)
        elif self.llm_name=='OpenAI':
            self.radio3.setChecked(True)
        elif self.llm_name=='Google':
            self.radio4.setChecked(True)

        self.api_buttonGroup = QButtonGroup()
        self.api_buttonGroup.addButton(self.radio1)
        self.api_buttonGroup.addButton(self.radio2)
        self.api_buttonGroup.addButton(self.radio3)
        self.api_buttonGroup.addButton(self.radio4)
        self.api_buttonGroup.buttonClicked.connect(self.radio_button_clicked)

        self.api_button_layout=QHBoxLayout()
        self.api_button_layout.addWidget(self.radio1)
        self.api_button_layout.addWidget(self.radio2)
        self.api_button_layout.addWidget(self.radio3)
        self.api_button_layout.addWidget(self.radio4)
        layout.addLayout(self.api_button_layout)

        self.api_info_layout=QHBoxLayout()
        self.api_llm_model_label=QLabel('具体模型名称')
        self.api_llm_model_input=QLineEdit()
        self.api_llm_model_input.setToolTip('请严格按照官方给出的名字填写')
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
        with open('../if_delete_audio_cache.txt', "r", encoding="utf-8") as f:
            try:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        if_delete = int(line)
                        break
            except Exception:
                if_delete = 0
                print("if_delete_audio_cache.txt的文件参数设置错误，应该输入一个数字！")
        if if_delete==0:
            self.radio_2_1.setChecked(True)
        else:
            self.radio_2_2.setChecked(True)
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
        with open('../is_fp32.txt', "r", encoding="utf-8") as f:
            try:
                if_fp16=not int(f.read())
            except Exception:
                if_fp16=0
                print("is_fp32.txt的文件参数设置错误")
        if not if_fp16:
            self.radio_3_1.setChecked(True)
        else:
            self.radio_3_2.setChecked(True)
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
        if os.path.exists('../reference_audio/GSV_sample_rate.txt'):
            with open('../reference_audio/GSV_sample_rate.txt', "r", encoding="utf-8") as f:
                try:
                    sampling_rate=int(f.read())
                except Exception:
                    sampling_rate = 16
                    print("sovits_sampling_steps.txt的文件参数设置错误，应该输入一个数字！")
        else:
            with open('../reference_audio/GSV_sample_rate.txt', "w", encoding="utf-8") as f:
                f.write('16')
            sampling_rate=16
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
            self.api_llm_model_input.setText('deepseek V3')
            self.api_llm_model_input.setDisabled(True)
        elif self.llm_name=='OpenAI':
            self.api_llm_model_input.setText(self.current_api_conf['llm_choose'][0]['model'])
            self.api_llm_model_input.setDisabled(False)
        elif self.llm_name=='Google':
            self.api_llm_model_input.setText(self.current_api_conf['llm_choose'][1]['model'])
            self.api_llm_model_input.setDisabled(False)

        if self.llm_name =='deepseek_up':
            self.api_key_input.setText('')
            self.api_key_input.setDisabled(True)
        elif self.llm_name=='deepseek_user':
            with open('../API Key.txt','r',encoding='utf-8') as f:
                api_key=f.read()
            self.api_key_input.setText(api_key)
            self.api_key_input.setDisabled(False)
        elif self.llm_name=='OpenAI':
            self.api_key_input.setText(self.current_api_conf['llm_choose'][0]['api_key'])
            self.api_key_input.setDisabled(False)
        elif self.llm_name=='Google':
            self.api_key_input.setText(self.current_api_conf['llm_choose'][1]['api_key'])
            self.api_key_input.setDisabled(False)

    def radio_button_clicked(self,button):
        if button.text()=='使用up的deepseek':
            self.llm_name='deepseek_up'
            self.current_api_conf['llm_choose'][0]['if_choose'] = False
            self.current_api_conf['llm_choose'][1]['if_choose'] = False
        elif button.text()=='你的deepseek API':
            self.llm_name='deepseek_user'
            self.current_api_conf['llm_choose'][0]['if_choose'] = False
            self.current_api_conf['llm_choose'][1]['if_choose'] = False
        elif button.text()=='OpenAI ChatGPT':
            self.llm_name='OpenAI'
            self.current_api_conf['llm_choose'][0]['if_choose'] = True
            self.current_api_conf['llm_choose'][1]['if_choose'] = False
        elif button.text()=='Google Gemini':
            self.llm_name='Google'
            self.current_api_conf['llm_choose'][0]['if_choose'] = False
            self.current_api_conf['llm_choose'][1]['if_choose'] = True

        self.set_api_conf_value()
        self.save_success_label.setText('')

    def save_config(self):
        if self.llm_name=='deepseek_up':
            with open('../API Key.txt','w',encoding='utf-8') as f:
                f.write('')
        elif self.llm_name=='deepseek_user':
            with open('../API Key.txt','w',encoding='utf-8') as f:
                f.write(self.api_key_input.text())
        elif self.llm_name=='OpenAI':
            self.current_api_conf['llm_choose'][0]['model']=self.api_llm_model_input.text()
            self.current_api_conf['llm_choose'][0]['api_key']=self.api_key_input.text()
        elif self.llm_name=='Google':
            self.current_api_conf['llm_choose'][1]['model']=self.api_llm_model_input.text()
            self.current_api_conf['llm_choose'][1]['api_key']=self.api_key_input.text()

        with open('../API_Choice.json','w',encoding='utf-8') as f:
            json.dump(self.current_api_conf,f,ensure_ascii=False,indent=4)

        if self.radio_2_1.isChecked():
            with open('../if_delete_audio_cache.txt','w',encoding='utf-8') as f:
                f.write('0')
        else:
            with open('../if_delete_audio_cache.txt','w',encoding='utf-8') as f:
                f.write('1')

        if self.radio_3_1.isChecked():
            with open('../is_fp32.txt','w',encoding='utf-8') as f:
                f.write('1')
        else:
            with open('../is_fp32.txt','w',encoding='utf-8') as f:
                f.write('0')

        if self.radio_4_1.isChecked():
            sampling_steps=4
        elif self.radio_4_2.isChecked():
            sampling_steps=8
        elif self.radio_4_3.isChecked():
            sampling_steps=16
        elif self.radio_4_4.isChecked():
            sampling_steps=32
        with open('../reference_audio/GSV_sample_rate.txt','w',encoding='utf-8') as f:
            f.write(str(sampling_steps))

        ordered_names=[]
        count = self.character_list_widget.count()
        for i in range(count):
            item = self.character_list_widget.item(i)
            ordered_names.append(item.text())
        order_data_to_save={
            "character_num": len(ordered_names),
            "character_names": ordered_names,
        }
        with open('../reference_audio/character_order.json','w',encoding='utf-8') as f:
            json.dump(order_data_to_save,f,ensure_ascii=False,indent=4)

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
    app = QApplication(sys.argv)
    win = conf_ui()

    win.show()
    sys.exit(app.exec_())
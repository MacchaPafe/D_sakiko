from random import randint

dialogWindowDefaultCss = f'''
        QWidget {{
            background-color: "#F0F4F9";
            color: "#7799CC";
        }}
        QDialog {{
            background-color: "#F0F4F9";
            color: "#7799CC";
        }}
        /* ================= 2. 卡片化容器 (GroupBox & Dialog) ================= */
        /* Fluent 风格的核心：白色悬浮卡片 */
        QGroupBox{{
            background-color: #FFFFFF; /* 纯白背景 */
            border: 1px solid #E5E5E5; /* 极细的灰色边框 */
            border-radius: 8px;        /* 较大的圆角 */
        }}

        QGroupBox {{
            margin-top: 50px; /* 留出标题空间 */
        }}

        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            top: 5px;
            color: "#7799CC";
            background-color: transparent;
        }}

        QPushButton {{
            background-color: #FFFFFF;
            border: 1px solid #D0D0D0;
            border-bottom: 1px solid #C0C0C0; /* 底部厚一点模拟立体感 */
            border-radius: 4px;
            color: "#7799CC";
            padding: 5px 15px;
        }}

        QPushButton:hover {{
            background-color: #F9F9F9;
            border-color: #C0C0C0;
        }}

        QPushButton:pressed {{
            background-color: #F0F0F0;
            border-top: 1px solid #C0C0C0; /* 按下时阴影反转 */
            border-bottom: 1px solid #D0D0D0;
            color: "#5F6368";
        }}
        
        QPushButton:disabled {{
            background-color: #E0E0E0;
            color: #D0D0D0;
            border-color: #E0E0E0;
        }}
        
        QLabel {{
            background-color: transparent; /* 标签背景透明 */
    
            padding: 5px 0; /* 增加内边距，使标签不显得拥挤 */
            color: #7799CC

        }}
        QToolButton {{
            color: #7799CC;
            background-color: transparent;
            border: none;
            border-radius: 4px;
        }}

        QToolButton:hover {{
            background-color: rgba(0, 0, 0, 0.05);
        }}
        
        QScrollBar:vertical {{
            border: none;
            background: #CAD4E4;
            width: 6px;
            margin: 0px 0px 0px 0px;
        }}

        QScrollBar::handle:vertical {{
            background: #7799CC;
            min-height: 20px;
            border-radius: 3px;
        }}

        QScrollBar::handle:vertical:hover {{
            background: #5F88BB;
        }}
        
        QListWidget {{
            background-color: #FFFFFF;
            border: 4px solid #E5E5E5;
            border-radius: 8px;
        }}
        '''

char_info_json={
  "香澄": {
    "theme_color": "#FF5522",
    "bestdori_index": 1,
    "romaji": "kasumi"
  },
  "多惠": {
    "theme_color": "#0077DD",
    "bestdori_index": 2,
    "romaji": "tae"
  },
  "里美": {
    "theme_color": "#FF55BB",
    "bestdori_index": 3,
    "romaji": "rimi"
  },
  "沙绫": {
    "theme_color": "#FFA30F",
    "bestdori_index": 4,
    "romaji": "saaya"
  },
  "有咲": {
    "theme_color": "#AA66DD",
    "bestdori_index": 5,
    "romaji": "arisa"
  },
  "美竹兰": {
    "theme_color": "#EE0022",
    "bestdori_index": 6,
    "romaji": "ran"
  },
  "摩卡": {
    "theme_color": "#00CCAA",
    "bestdori_index": 7,
    "romaji": "moca"
  },
  "绯玛丽": {
    "theme_color": "#FF9999",
    "bestdori_index": 8,
    "romaji": "himari"
  },
  "巴": {
    "theme_color": "#BB0033",
    "bestdori_index": 9,
    "romaji": "tomoe"
  },
  "羽泽鸫": {
    "theme_color": "#FFEE88",
    "bestdori_index": 10,
    "romaji": "tsugumi"
  },
  "弦卷心": {
    "theme_color": "#FFEE22",
    "bestdori_index": 11,
    "romaji": "kokoro"
  },
  "濑田薰": {
    "theme_color": "#AA33CC",
    "bestdori_index": 12,
    "romaji": "kaoru"
  },
  "育美": {
    "theme_color": "#FF9922",
    "bestdori_index": 13,
    "romaji": "hagumi"
  },
  "花音": {
    "theme_color": "#44DDFF",
    "bestdori_index": 14,
    "romaji": "kanon"
  },
  "美咲": {
    "theme_color": "#006699",
    "bestdori_index": 15,
    "romaji": "misaki"
  },
  "丸山彩": {
    "theme_color": "#FF88BB",
    "bestdori_index": 16,
    "romaji": "aya"
  },
  "日菜": {
    "theme_color": "#55DDEE",
    "bestdori_index": 17,
    "romaji": "hina"
  },
  "千圣": {
    "theme_color": "#FFEEAA",
    "bestdori_index": 18,
    "romaji": "chisato"
  },
  "麻弥": {
    "theme_color": "#99DD88",
    "bestdori_index": 19,
    "romaji": "mami"
  },
  "伊芙": {
    "theme_color": "#DDBBFF",
    "bestdori_index": 20,
    "romaji": "eve"
  },
  "友希那": {
    "theme_color": "#881188",
    "bestdori_index": 21,
    "romaji": "yukina"
  },
  "纱夜": {
    "theme_color": "#00AABB",
    "bestdori_index": 22,
    "romaji": "sayo"
  },
  "莉莎": {
    "theme_color": "#DD2200",
    "bestdori_index": 23,
    "romaji": "lisa"
  },
  "亚子": {
    "theme_color": "#DD0088",
    "bestdori_index": 24,
    "romaji": "ako"
  },
  "燐子": {
    "theme_color": "#BBBBBB",
    "bestdori_index": 25,
    "romaji": "rinko"
  },
  "真白": {
    "theme_color": "#6677CC",
    "bestdori_index": 26,
    "romaji": "mashiro"
  },
  "透子": {
    "theme_color": "#EE6666",
    "bestdori_index": 27,
    "romaji": "toko"
  },
  "七深": {
    "theme_color": "#EE7744",
    "bestdori_index": 28,
    "romaji": "nanami"
  },
  "筑紫": {
    "theme_color": "#EE7788",
    "bestdori_index": 29,
    "romaji": "tsukushi"
  },
  "瑠唯": {
    "theme_color": "#669988",
    "bestdori_index": 30,
    "romaji": "rui"
  },
  "layer": {
    "theme_color": "#CC0000",
    "bestdori_index": 31,
    "romaji": "layer"
  },
  "六花": {
    "theme_color": "#AAEE22",
    "bestdori_index": 32,
    "romaji": "lock"
  },
  "msk": {
    "theme_color": "#EEBB44",
    "bestdori_index": 33,
    "romaji": "masking"
  },
  "pareo": {
    "theme_color": "#FF99BB",
    "bestdori_index": 34,
    "romaji": "pareo"
  },
  "chu2": {
    "theme_color": "#00BBFF",
    "bestdori_index": 35,
    "romaji": "chuchu"
  },
  "灯": {
    "theme_color": "#77BBDD",
    "bestdori_index": 36,
    "romaji": "tomori"
  },
  "爱音": {
    "theme_color": "#FF8899",
    "bestdori_index": 37,
    "romaji": "anon"
  },
  "乐奈": {
    "theme_color": "#77DD77",
    "bestdori_index": 38,
    "romaji": "rana"
  },
  "素世": {
    "theme_color": "#FFDD88",
    "bestdori_index": 39,
    "romaji": "soyo"
  },
  "立希": {
    "theme_color": "#7777AA",
    "bestdori_index": 40,
    "romaji": "taki"
  },
  "初华": {
    "theme_color": "#BB9955",
    "bestdori_index": 337,
    "romaji": "uika"
  },
  "若叶睦": {
    "theme_color": "#779977",
    "bestdori_index": 338,
    "romaji": "mutsumi"
  },
  "海铃": {
    "theme_color": "#335566",
    "bestdori_index": 339,
    "romaji": "umiri"
  },
  "喵梦": {
    "theme_color": "#AA4477",
    "bestdori_index": 340,
    "romaji": "nyamu"
  },
  "祥子": {
    "theme_color": "#7799CC",
    "bestdori_index": 341,
    "romaji": "sakiko"
  }
}

class CurrentConfig:
    selected_existing_character:str="kasumi"
    selected_existing_character_name:str="香澄"
    bestdori_chara_index:int=1
    bestdori_char_name:str="香澄"
    download_for_existing_char:bool=True

    def __call__(self):
        print("选择的包内角色:",self.selected_existing_character,' ',self.selected_existing_character_name)
        print("Bestdori 角色编号:",self.bestdori_chara_index,' ',self.bestdori_char_name)
        print("为已存在角色下载:",self.download_for_existing_char)

import os,shutil,glob,json
class AddCostume:
    @staticmethod
    def add_costume_for_existed_char(char_folder_name: str,internal_live2d_name: str,costume_name: str):
        save_folder_path=f"../live2d_related/{char_folder_name}/extra_model"
        new_costume_path=f"{save_folder_path}/{costume_name}"
        if not os.path.exists(save_folder_path):    #创建extra_model文件夹
            os.makedirs(save_folder_path)
        if not os.path.exists(new_costume_path):    #创建服装文件夹
            os.makedirs(new_costume_path)
        if not os.path.exists(f"./.model_download_cache/{internal_live2d_name}"):    #检查缓存文件夹是否存在
            raise FileNotFoundError(f"未找到已下载服装的缓存文件夹，下载过程可能出现了某些未知错误:./.model_download_cache/{internal_live2d_name}!")

        #首先从默认模型文件夹中复制除moc和贴图的文件
        default_model_path=f"../live2d_related/{char_folder_name}/live2D_model"
        all_copy_files=[]
        model_json=glob.glob(os.path.join(default_model_path,f"*.model.json"))[0]
        all_copy_files.append(model_json)
        mtns=glob.glob(os.path.join(default_model_path,f"*.mtn"))
        all_copy_files.extend(mtns)
        exps=glob.glob(os.path.join(default_model_path,f"*.exp.json"))
        all_copy_files.extend(exps)
        for file in all_copy_files:
            shutil.copy(file,new_costume_path)
        #然后从下载缓存文件夹中复制下载到的文件
        cache_mocs=glob.glob(os.path.join(f"./.model_download_cache/{internal_live2d_name}",f"*.moc"))
        for file in cache_mocs:
            shutil.copy(file,new_costume_path)
        cache_physics=glob.glob(os.path.join(f"./.model_download_cache/{internal_live2d_name}",f"*.physics.json"))
        for file in cache_physics:
            shutil.copy(file,new_costume_path)
        cache_textures=glob.glob(os.path.join(f"./.model_download_cache/{internal_live2d_name}/textures",f"*.png"))
        for file in cache_textures:
            shutil.copy(file,new_costume_path)
        cache_motions=glob.glob(os.path.join(f"./.model_download_cache/{internal_live2d_name}/motions",f"*.mtn"))
        for file in cache_motions:
            shutil.copy(file,new_costume_path)
        cache_expressions=glob.glob(os.path.join(f"./.model_download_cache/{internal_live2d_name}/expressions",f"*.exp.json"))
        for file in cache_expressions:
            shutil.copy(file,new_costume_path)
        #修改新的model.json文件
        with open(f"{new_costume_path}/{os.path.basename(model_json)}",'r',encoding='utf-8') as f:
            model_json_content=json.load(f)
        model_json_content["model"]=f"{os.path.basename(cache_mocs[0])}"
        model_json_content["textures"]=[f"{os.path.basename(tex)}" for tex in cache_textures]
        if cache_physics:
            model_json_content["physics"]=f"{os.path.basename(cache_physics[0])}"
            if "physics_v2" in model_json_content:
                model_json_content["physics_v2"]= {"file":f"{os.path.basename(cache_physics[0])}"}
        else:
            model_json_content.pop("physics", None)
            model_json_content.pop("physics_v2", None)
        with open(f"{new_costume_path}/{os.path.basename(model_json)}",'w',encoding='utf-8') as f:
            json.dump(model_json_content,f, indent=4, ensure_ascii=False)

    @staticmethod
    def add_costume_for_new_character(character_ui_name,character_folder_name,live2d_name):
        if os.path.exists(f"../live2d_related/{character_folder_name}"):
            raise FileExistsError(f"角色{character_folder_name}已存在，无法作为新角色添加服装!")
        if not os.path.exists(f"./.model_download_cache/{live2d_name}"):    #检查缓存文件夹是否存在
            raise FileNotFoundError(f"未找到已下载服装的缓存文件夹，下载过程可能出现了某些未知错误:./.model_download_cache/{live2d_name}!")
        os.makedirs(f"../live2d_related/{character_folder_name}")
        with open(f"../live2d_related/{character_folder_name}/name.txt",'w',encoding='utf-8') as f:
            f.write(character_ui_name)
        with open(f"../live2d_related/{character_folder_name}/character_description.txt",'w',encoding='utf-8') as f:
            f.write("在这里完善角色："+character_ui_name+" 的介绍吧！")
        save_model_folder_path=f"../live2d_related/{character_folder_name}/live2D_model"
        os.makedirs(save_model_folder_path)
        #从从下载缓存文件夹中复制下载到的文件
        cache_mocs=glob.glob(os.path.join(f"./.model_download_cache/{live2d_name}",f"*.moc"))
        for file in cache_mocs:
            shutil.copy(file,save_model_folder_path)
        cache_physics=glob.glob(os.path.join(f"./.model_download_cache/{live2d_name}",f"*.physics.json"))
        for file in cache_physics:
            shutil.copy(file,save_model_folder_path)
        cache_textures=glob.glob(os.path.join(f"./.model_download_cache/{live2d_name}/textures",f"*.png"))
        for file in cache_textures:
            shutil.copy(file,save_model_folder_path)
        cache_motions=glob.glob(os.path.join(f"./.model_download_cache/{live2d_name}/motions",f"*.mtn"))
        for file in cache_motions:
            shutil.copy(file,save_model_folder_path)
        cache_expressions=glob.glob(os.path.join(f"./.model_download_cache/{live2d_name}/expressions",f"*.exp.json"))
        for file in cache_expressions:
            shutil.copy(file,save_model_folder_path)
        #创建model.json文件
        model_json_content={"model":f"{os.path.basename(cache_mocs[0])}",
                            "textures":[f"{os.path.basename(tex)}" for tex in cache_textures],
                            }
        if cache_physics:
            model_json_content["physics"]=f"{os.path.basename(cache_physics[0])}"
            model_json_content["physics_v2"]= {"file":f"{os.path.basename(cache_physics[0])}"}

        possible_motion_groups={
            "happiness":["smile","kime","nnf03","nnf04"],
            "sadness":["sad","cry","serious"],
            "anger":["angry","serious"],
            "disgust":["angry","serious","scared"],
            "like":["smile","kime","jaan","gattsu","oowarai","nnf04"],
            "surprise":["surprised","scared","jaan","oowarai","odoodo"],
            "fear":["scared","cry","serious","sneeze","odoodo"],
            "IDLE":["kime","nnf","smile01","wink","sleep","niyaniya","nf_left","nf_right"],
            "text_generating":["thinking","eeto"],
            "bye":["bye","wink","smile"],
            "change_character":["bye","smile","kime","shame","gattsu","jaan"],
            "idle_motion":["idle","smile"],
            "talking_motion":["nnf02","nod"]
        }
        motions={}
        count=0
        stop_count_value=[5,11,17,23,29,35,41,50,53,55,58,59,60]
        for i,(group_name,possible_motion_list) in enumerate(possible_motion_groups.items()):
            motions[group_name]=[]
            for possible_motion in possible_motion_list:
                for motion_file in cache_motions:
                    if os.path.basename(motion_file).startswith(possible_motion):
                        motions[group_name].append({"name":f"{group_name}_{count}","file":os.path.basename(motion_file)})
                        count+=1
                        if count == stop_count_value[i]:
                            break
                if count == stop_count_value[i]:
                    break
            if count != stop_count_value[i]:   #这一组没有填充指定数量的动作
                count=stop_count_value[i]
                if not motions[group_name]:   #如果这一组一个动作都没有
                    motions[group_name].append({"name":f"{group_name}_{count}","file":os.path.basename(cache_motions[randint(0,len(cache_motions)-1)])})
        model_json_content["motions"]=motions
        with open(f"{save_model_folder_path}/3.model.json",'w',encoding='utf-8') as f:
            json.dump(model_json_content,f, indent=4, ensure_ascii=False)

        #创建reference_audio内的空文件夹
        os.makedirs(f"../reference_audio/{character_folder_name}",exist_ok=True)
        os.makedirs(f"../reference_audio/{character_folder_name}/GPT-SoVITS_models",exist_ok=True)
        with open(f"../reference_audio/{character_folder_name}/GPT-SoVITS_models/在这里放入角色的GPT-SoVITS模型（.pth和.ckpt）",'w',encoding='utf-8') as f:
            f.write('')
        with open(f"../reference_audio/{character_folder_name}/QT_style.json",'w',encoding='utf-8') as f:
            f.write('''QWidget {
                    color: #77DD77;
                    }''')
        with open(f"../reference_audio/{character_folder_name}/reference_audio_language.txt",'w',encoding='utf-8') as f:
            f.write('''#填写说明：只需把下面的数字3修改为其他数字即可，范围为1-11，分别对应你的参考音频语言为   1：中文  2：英文  3：日文  4：粤语  5：韩文  6：中英混合  7：日英混合  8：粤英混合  9：韩英混合  10：多语种混合  11：多语种混合（粤语） 
#不能有任何其他内容，包括空格、换行、缩进等，否则会报错
3''')
        with open(f"../reference_audio/{character_folder_name}/reference_text.txt",'w',encoding='utf-8') as f:
            f.write('')







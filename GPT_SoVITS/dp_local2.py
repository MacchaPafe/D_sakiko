import time,os
from copy import deepcopy

import json
import litellm
from litellm import completion

from qconfig import d_sakiko_config, THIRD_PARTY_OPENAI_COMPAT_PROVIDER_IDS
from llm_model_utils import ensure_openai_compatible_model


class DSLocalAndVoiceGen:
    def __init__(self,characters):
        self.character_list=characters

        self.all_character_msg=[]

        self.audio_language = ["中英混合", "日英混合"]
        self.audio_language_choice = self.audio_language[1]
        self.is_think_content_output_complete=False
        self.model=__import__('live2d_1').get_live2d()
        self.sakiko_state=True
        self.if_generate_audio=True
        self.current_char_index=0
        self.if_sakiko=False
        self.idle_texts = ["等待话题中...", "新的对话内容是...?", "就绪", "倾听中..."]
        self.initial()

    def initial(self):
        for character in self.character_list:
            self.all_character_msg.append([{"role": "system",
                                            "content": f'{character.character_description}'}])

        if os.path.getsize('../reference_audio/history_messages_dp.json')!=0:
            with open('../reference_audio/history_messages_dp.json','r',encoding='utf-8') as f:
                json_data=json.load(f)
            for index, character in enumerate(self.character_list):
                for data in json_data:
                    if data['character'] == character.character_name:
                        self.all_character_msg[index] = data['history']
                        # 之前可能错误的将第一条消息的类型写为了 user；如果是这样的话，改为 system。
                        if self.all_character_msg[index][0]['role'] == 'user':
                            self.all_character_msg[index][0]['role']='system'


        if self.character_list[self.current_char_index].character_name == '祥子':
            self.if_sakiko = True
        else:
            self.if_sakiko = False

        with open('./text/restr.rep', 'r', encoding='utf-8') as f:
            self.restr = f.read()


    def change_character(self):
        if len(self.character_list) == 1:
            self.current_char_index = 0
        else:
            if self.current_char_index < len(self.character_list) - 1:
                self.current_char_index += 1
            else:
                self.current_char_index = 0
        if self.character_list[self.current_char_index].character_name == '祥子':
            self.if_sakiko = True
        else:
            self.if_sakiko = False

    def trim_list_to_340kb(self, data_list):
        working_list=deepcopy(data_list)
        MAX_SIZE = 340 * 1024  #主流大模型的上下文现在基本都超过了100万token，差不多500KB，这里设置340KB以防万一
        while len(json.dumps(data_list, ensure_ascii=False).encode('utf-8')) > MAX_SIZE:
            del working_list[1]
        return working_list

    @staticmethod
    def concat_provider_and_model(provider: str, model: str) -> str:
        """
        智能的将模型提供商和模型名称连接在一起。
        如果 model 不包含 / 符号，那么就将 provider 和 model 用 / 连接起来。
        如果 model 已经包含 / 符号，我们认为此时包含了提供商信息，那么就直接返回 model。

        :param provider: 模型提供商，如 openai、deepseek
        :param model: 模型名称，如 gpt-4、deepseek-chat。也可以输入 deepseek/deepseek-chat 这种带提供商前缀的完整名称。此时，输入的 provider 会被忽略。
        """
        if '/' in model:
            return model
        else:
            return f"{provider}/{model}"

    @staticmethod
    def normalize_model_for_provider(provider: str, model: str) -> str:
        """根据 provider 类型决定是否尝试自动补全前缀。

        - 第三方 OpenAI 兼容端点：强制走 openai/ 路由（若未带 openai/ 则自动补齐）。
        - litellm 内置 provider：若用户未输入前缀，则尝试补全为 provider/model。
        """
        if provider in THIRD_PARTY_OPENAI_COMPAT_PROVIDER_IDS:
            return ensure_openai_compatible_model(model)
        return DSLocalAndVoiceGen.concat_provider_and_model(provider, model)

    def report_message_to_main_ui(self, message_queue, is_text_generating_queue, message: str):
        """
        向主界面的消息栏汇报一条错误信息，并且删除最新正在发给模型的对话记录。
        """
        message_queue.put(message)
        is_text_generating_queue.get()
        # 删除未能成功发送的信息
        self.all_character_msg[self.current_char_index].pop()
        # 源代码如此，我也不知道为啥要休息一会
        time.sleep(2)

    def text_generator(self,
                       text_queue,
                       is_audio_play_complete,
                       is_text_generating_queue,
                       dp2qt_queue,
                       qt2dp_queue,
                       message_queue,
                       char_is_converted_queue,
                       change_char_queue,
                       AudioGenerator):

        while True:
            #user_input = input(">>>输入bye退出聊天，输入“lan”更改语音语言，输入“model”更改LLM\n"
                               #+ f">>>当前模型：{self.model_choice}  语音语言：{self.audio_language_choice}\n>>>")
            while not AudioGenerator.is_change_complete:
                time.sleep(0.4)

            #message_queue.put(f"输入bye：退出程序	l：切换语言	m：更改LLM\n"+("conv：切换祥子状态	"if self.if_sakiko else '')+"clr：清空聊天记录	v：关闭/开启语音\n当前语言："+("中文" if self.audio_language_choice=="中英混合" else "日文")+ "		语音："+("开启" if self.if_generate_audio else "关闭")+('	mask...'if self.sakiko_state and self.if_sakiko else ''))
            message_queue.put(self.idle_texts[random.randint(0,len(self.idle_texts)-1)])
            #message_queue.put("语音："+("开启" if self.if_generate_audio else "关闭")+"语言："+("中文" if self.audio_language_choice=="中英混合" else "日文"))
            while True:
                if not qt2dp_queue.empty():
                    user_input=qt2dp_queue.get()
                    break
                time.sleep(1)
            # --------------------前面的一些判断逻辑

            if user_input == 'bye':
                text_queue.put('bye')
                break

            elif user_input == 'mask':
                if self.if_sakiko:
                    char_is_converted_queue.put('maskoff')
                else:
                    message_queue.put("祥子好像不在<w>")
                time.sleep(2)
                continue
            elif user_input == 'conv':
                if self.if_sakiko:
                    self.sakiko_state=not self.sakiko_state
                    message_queue.put("已切换为"+("黑祥"if self.sakiko_state else "白祥"))
                    char_is_converted_queue.put(self.sakiko_state)
                else:
                    message_queue.put("祥子好像不在<w>")
                time.sleep(2)
                continue
            elif user_input == 'v':
                self.if_generate_audio = not self.if_generate_audio
                message_queue.put("已" + ("开启" if self.if_generate_audio else "关闭") + "语音合成")
                time.sleep(2)
                continue
            elif user_input == 's':
                self.change_character()
                message_queue.put(
                    f"已切换为：{self.character_list[self.current_char_index].character_name}\n正在切换GPT-SoVITS模型...")
                change_char_queue.put('yes')
                dp2qt_queue.put("changechange")
                AudioGenerator.change_character()
                time.sleep(2)
                continue
            elif user_input == 'clr':
                self.all_character_msg[self.current_char_index] = [{"role": "system",
                                                                    "content": f'{self.character_list[self.current_char_index].character_description}'}]
                message_queue.put("已清空角色的聊天记录")
                time.sleep(2)
                continue
            elif user_input in ['start_talking', 'stop_talking']:
                change_char_queue.put(user_input)
                continue
            elif user_input == 'change_l2d_background':
                change_char_queue.put('change_l2d_background')
                continue
            elif user_input.startswith('change_l2d_model'):
                change_char_queue.put(user_input)
                continue

            if user_input == 'l':
                self.audio_language_choice = "中英混合" if self.audio_language_choice == '日英混合' else '日英混合'
                message_queue.put("已切换语言为："+("中文" if self.audio_language_choice=='中英混合' else "日文"))
                time.sleep(2)
                continue

            if self.audio_language_choice=='日英混合':
                user_input=user_input+'（本句话你的回答请务必用日语，并且请将额外的中文翻译内容放到“[翻译]”这一标记格式之后，并以“[翻译结束]”作为翻译的结束标志，每三句话翻译一次！请务必严格遵守这一格式回答！！）'
            else:
                user_input = user_input + '（本句话你的回答请务必全部用中文，一定不要有日语假名！）'

            if self.if_sakiko:
                if self.sakiko_state:
                    user_input = user_input +'（本句话用黑祥语气回答!）'
                else:
                    user_input = user_input + '（本句话用白祥语气回答!）'

            # -----------------------------
            user_this_turn_msg = {"role": "user",
                                  "content": user_input}
            self.all_character_msg[self.current_char_index].append(user_this_turn_msg)
            to_llm_msg=deepcopy(self.all_character_msg[self.current_char_index])
            to_llm_msg[0]['content']+=self.restr
            message_queue.put(f"{self.character_list[self.current_char_index].character_name}思考中...")
            is_text_generating_queue.put('no_complete')		#正在生成文字的标志
            # --------------------------
            # 优先处理使用 Up API 的情况

            try:
                if d_sakiko_config.use_default_deepseek_api.value:
                    print("使用 UP 的 DeepSeek API 进行对话生成")
                    response = completion(
                        model="deepseek/deepseek-chat",
                        messages=self.trim_list_to_340kb(self.all_character_msg[self.current_char_index]),
                        api_key=self.model
                    )
                # 第二优先级是检查自定义 API Url
                # 只要存在自定义 API，就使用自定义 API
                elif d_sakiko_config.enable_custom_llm_api_provider.value:
                    print("使用自定义大模型 API 进行对话生成")
                    print("API Base: ", d_sakiko_config.custom_llm_api_url.value)
                    print("API Model: ", d_sakiko_config.custom_llm_api_model.value)
                    response = completion(
                        # 自定义 API 与 OpenAI 兼容，litellm 需要通过 openai/ 前缀路由
                        model=ensure_openai_compatible_model(d_sakiko_config.custom_llm_api_model.value),
                        messages=self.trim_list_to_340kb(self.all_character_msg[self.current_char_index]),
                        api_key=d_sakiko_config.custom_llm_api_key.value,
                        # 自定义 API 地址
                        base_url=d_sakiko_config.custom_llm_api_url.value
                    )
                # 最后：使用选择的预定义 API 提供商
                else:
                    print("使用预定义大模型 API 进行对话生成")
                    print("Provider: ", d_sakiko_config.llm_api_provider.value)
                    print("Model: ", d_sakiko_config.llm_api_model.value[d_sakiko_config.llm_api_provider.value])
                    provider = d_sakiko_config.llm_api_provider.value
                    model = d_sakiko_config.llm_api_model.value.get(provider, "")
                    api_key = d_sakiko_config.llm_api_key.value.get(provider, "")
                    base_url = d_sakiko_config.llm_api_base_url.value.get(provider)

                    completion_kwargs = {}
                    if base_url:
                        completion_kwargs["base_url"] = base_url

                    response = completion(
                        model=self.normalize_model_for_provider(provider, model),
                        messages=self.trim_list_to_340kb(self.all_character_msg[self.current_char_index]),
                        api_key=api_key,
                        **completion_kwargs,
                    )
            except litellm.exceptions.Timeout:
                self.report_message_to_main_ui(
                    message_queue,
                    is_text_generating_queue,
                    "请求超时，请检查网络连接或稍后再试。"
                )
                continue
            except litellm.exceptions.AuthenticationError:
                self.report_message_to_main_ui(
                    message_queue,
                    is_text_generating_queue,
                    "API Key 认证失败，请检查 Key 是否正确。"
                )
                continue
            except litellm.exceptions.RateLimitError:
                self.report_message_to_main_ui(
                    message_queue,
                    is_text_generating_queue,
                    "请求过于频繁，请稍后再试。"
                )
                continue
            except litellm.exceptions.APIConnectionError:
                self.report_message_to_main_ui(
                    message_queue,
                    is_text_generating_queue,
                    "与大模型网站建立连接失败，请检查网络。"
                )
                continue
            # 特殊捕获一个 API Key 余额不足的错误
            except litellm.exceptions.BadRequestError as e:
                # 悲伤的是，litellm 把异常封装的过头了，根本获得不了原始的状态码
                # 特殊处理 DeepSeek 无余额时返回的内容；其他 API 接口我也不清楚是否能捕获
                if "Insufficient Balance" in e.message:
                    self.report_message_to_main_ui(
                        message_queue,
                        is_text_generating_queue,
                        "账户余额不足，如果正在用up的api，请联系UP充值"
                    )
                else:
                    self.report_message_to_main_ui(
                        message_queue,
                        is_text_generating_queue,
                        f"未知的请求错误，状态码：{e.status_code}。"
                    )
                continue
            except litellm.exceptions.PermissionDeniedError:
                self.report_message_to_main_ui(
                    message_queue,
                    is_text_generating_queue,
                    f"无法访问所请求的模型，请检查是否有权限使用该模型，或余额是否足够"
                )
                continue
            except Exception:
                self.report_message_to_main_ui(
                    message_queue,
                    is_text_generating_queue,
                    "出现了未知错误，请再试一次。"
                )
                import traceback
                traceback.print_exc()

                continue

                # if response.status_code == 200:
                # 	pass
                # else:
                # 	time.sleep(2)
                # 	if response.status_code==402:
                # 		message_queue.put("账户余额不足。本次对话未能成功。")
                # 	elif response.status_code==401:
                # 		message_queue.put("API key认证出错，请检查正确性")
                # 	elif response.status_code==429:
                # 		message_queue.put("请求速度太快，被限制了（应该不会出现这个错误吧，")
                # 	elif response.status_code==500 or response.status_code==503:
                # 		message_queue.put("API服务器可能崩了，可等待一会后重试。")
                # 	elif response.status_code==403:
                # 		message_queue.put("出现地区错误，试试科学上网...")
                # 	else:
                # 		message_queue.put("出现了未知错误，可尝试重新对话一次。")
                # 	is_text_generating_queue.get()
                # 	self.all_character_msg[self.current_char_index].pop()
                # 	time.sleep(2)
                # 	continue

            model_this_turn_msg = {"role": "assistant", "content": response.choices[0].message.content.strip()}
            #print("aaaaaaaaaaaaaaaa",cleaned_text_of_model_response,'bbbbbbbbbbbbbbbbbb')
            text_queue.put(response.choices[0].message.content.strip())

            self.all_character_msg[self.current_char_index].append(model_this_turn_msg)

            while is_audio_play_complete.get() is None:
                time.sleep(0.5)		#不加就无法正常运行

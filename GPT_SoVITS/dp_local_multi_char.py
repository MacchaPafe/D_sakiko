import time, os

import litellm

from qconfig import d_sakiko_config
from litellm import completion


class DSLocalAndVoiceGen:
    def __init__(self, characters):
        self.character_list = characters
        self.current_character_num = [0, 1]

        self.audio_language = ["中英混合", "日英混合"]
        self.audio_language_choice = self.audio_language[1]
        self.is_think_content_output_complete = False
        self.model = __import__('live2d_1').get_live2d()
        self.sakiko_state = True
        self.if_generate_audio = True
        # self.current_char_index=0
        self.if_sakiko = False
        self.base_prompt = '''
								# Role
								你是一位精通心理学和戏剧创作的资深编剧，擅长创作符合ACG角色设定的沉浸式对话（小剧场）。

								# Goal
								你需要根据用户提供的两位角色的详细设定，编写一段两人之间的对话。

								# Rules
								1. **性格沉浸**：严格遵守两人的说话习惯、心理状态。
								2. **关系体现**：对话要体现两人过去的历史纠葛和当前的某种关系（如：尴尬、亲密、敌对、表面客气但内心疏离等）。
								3. **格式严格**：必须只返回一个 JSON 列表，不要包含任何 Markdown 标记或其他废话。

								# Output Format (JSON)
								[
								  {
									"speaker": "角色名",
									"emotion": "仅可以从happiness/sadness/anger/fear/like/disgust/surprise这几个选项中选择！",
									"text": "角色的台词内容（日语）",
									"translation": "台词的中文翻译"
								  },
								  ...
								]
								'''

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
    def report_message_to_main_ui(message_queue, text_queue, message: str):
        """
        向主界面的消息栏汇报一条错误信息，并且删除最新正在发给模型的对话记录。
        """
        message_queue.put(message)
        # 返回一个“错误”了的信息
        text_queue.put('error')
        # 源代码如此，我也不知道为啥要休息一会
        time.sleep(2)

    def text_generator(self,
                       text_queue,
                       qt2dp_queue,
                       message_queue,
                       ):

        while True:
            while True:
                if not qt2dp_queue.empty():
                    data = qt2dp_queue.get()
                    if data == 'EXIT':
                        user_input = 'EXIT'
                        break
                    self.current_character_num = data['char_index']
                    user_input = data['user_input']
                    if not user_input:
                        user_input = {'character_0': {'talk_style': '', 'interaction_details': ''},
                                      'character_1': {'talk_style': '', 'interaction_details': ''},
                                      'situation': ''}
                    break
                time.sleep(0.2)
            if user_input == 'EXIT':
                break

            user_prompt = f'''
                请根据以下信息生成对话：

                ### 【关于角色A】
                - **姓名**：{self.character_list[self.current_character_num[0]].character_name}
                - **角色描述**：{self.character_list[self.current_character_num[0]].character_description} 
                - **说话风格**：{user_input['character_0']['talk_style']} 

                ### 【关于角色B】
                - **姓名**：{self.character_list[self.current_character_num[1]].character_name}
                - **角色描述**：{self.character_list[self.current_character_num[1]].character_description} 
                - **说话风格**：{user_input['character_1']['talk_style']}

                ### 【当前情境/话题】
                {user_input['situation']}

                ### 【两角色之间的互动细节】
                角色{self.character_list[self.current_character_num[0]].character_name}:{user_input['character_0']['interaction_details']}
                角色{self.character_list[self.current_character_num[1]].character_name}:{user_input['character_1']['interaction_details']}

                ### 【要求】
                - 对话轮数：15轮左右。
                - 语言：日语，携带同步中文翻译，放到JSON指定的字段中。
                - 请打破‘一人一句’的轮流发言限制，允许同一角色连续多次发言。为了体现真实感，如果角色在一段话中出现了明显的情绪转折、语气停顿或话题递进，请务必将其拆分为两个或多个连续的 JSON 块（例如：A -> A -> B -> A -> A）。不要把所有内容硬塞进一条消息里。
                - 格式：务必严格按照指定的JSON格式输出，不要有多余的文字说明！！

                现在开始生成JSON。
            '''

            user_this_turn_msg = [{"role": "system", "content": self.base_prompt},
                                  {"role": "user", "content": user_prompt}]
            message_queue.put("生成文本中...")
            time.sleep(2)
            test_text = '''
            [
  {
    "speaker": "香澄",
    "emotion": "surprise",
    "text": "あら？楽奈ちゃん！",
    "translation": "哎呀？乐奈酱！"
  },
  {
    "speaker": "香澄",
    "emotion": "happiness",
    "text": "どうしたの？RiNGに一人で来るなんて珍しいね！",
    "translation": "怎么了？一个人来RiNG真是少见呢！"
  },
  {
    "speaker": "楽奈",
    "emotion": "like",
    "text": "香澄。",
    "translation": "香澄。"
  },
  {
    "speaker": "楽奈",
    "emotion": "like",
    "text": "ギターを弾きたい。",
    "translation": "想弹吉他。"
  },
  {
    "speaker": "香澄",
    "emotion": "happiness",
    "text": "ええ！もちろんいいよ！",
    "translation": "诶！当然可以啦！"
  }
]
            '''
            # time.sleep(2)
            # text_queue.put(test_text)
            # continue
            # --------------------------
            try:
                if d_sakiko_config.use_default_deepseek_api.value:
                    response = completion(
                        model="deepseek/deepseek-chat",
                        messages=user_this_turn_msg,
                        api_key=self.model
                    )
                # 第二优先级是检查自定义 API Url
                # 只要存在自定义 API，就使用自定义 API
                elif d_sakiko_config.enable_custom_llm_api_provider.value:
                    response = completion(
                        model=d_sakiko_config.custom_llm_api_model.value,
                        messages=user_this_turn_msg,
                        api_key=d_sakiko_config.custom_llm_api_key.value,
                        # 自定义 API 地址
                        base_url=d_sakiko_config.custom_llm_api_url.value
                    )
                # 最后：使用选择的预定义 API 提供商
                else:
                    response = completion(
                        model=self.concat_provider_and_model(d_sakiko_config.llm_api_provider.value,
                                                             d_sakiko_config.llm_api_model.value[
                                                                 d_sakiko_config.llm_api_provider.value]),
                        messages=user_this_turn_msg,
                        api_key=d_sakiko_config.llm_api_key.value[d_sakiko_config.llm_api_provider.value]
                    )
            except litellm.exceptions.Timeout:
                self.report_message_to_main_ui(
                    message_queue,
                    text_queue,
                    "请求超时，请检查网络连接或稍后再试。"
                )
                continue
            except litellm.exceptions.AuthenticationError:
                self.report_message_to_main_ui(
                    message_queue,
                    text_queue,
                    "API Key 认证失败，请检查 Key 是否正确。"
                )
                continue
            except litellm.exceptions.RateLimitError:
                self.report_message_to_main_ui(
                    message_queue,
                    text_queue,
                    "请求过于频繁，请稍后再试。"
                )
                continue
            except litellm.exceptions.APIConnectionError:
                self.report_message_to_main_ui(
                    message_queue,
                    text_queue,
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
                        text_queue,
                        "账户余额不足，如果正在用up的api，请联系UP充值"
                    )
                else:
                    self.report_message_to_main_ui(
                        message_queue,
                        text_queue,
                        f"未知的请求错误，状态码：{e.status_code}。"
                    )
                continue
            except litellm.exceptions.PermissionDeniedError:
                self.report_message_to_main_ui(
                    message_queue,
                    text_queue,
                    f"无法访问所请求的模型，请检查是否有权限使用该模型，或余额是否足够"
                )
                continue
            except Exception:
                self.report_message_to_main_ui(
                    message_queue,
                    text_queue,
                    "出现了未知错误，请再试一次。"
                )
                import traceback
                traceback.print_exc()

                continue

            # 去掉多余的字符，使用正则表达式
            response_json = response.choices[0].message.content.strip()
            text_queue.put(response_json)


if __name__ == "__main__":
    import sys

    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    import character
    from queue import Queue

    get_all = character.GetCharacterAttributes()
    characters = get_all.character_class_list
    ds_local_and_voice_gen = DSLocalAndVoiceGen(characters)
    # user_input={'character_0':{'talk_style':'素世是香澄的后辈，但尽管如此，香澄还是会保持她活泼以及热情高涨的说话风格。','interaction_details':'香澄也许会直接称呼素世为そよちゃん'},
    # 			'character_1':{'talk_style':'素世在面对前辈时会保持一个沉稳且尊重的态度。','interaction_details':'素世应该会称呼香澄为戸山先輩'},
    # 			'situation':'两人周末在乐器店相遇。'}
    # user_input = {
    # 	'character_0': {'talk_style': '素世在面对爱音时有时会带一点不耐烦，有时也会很关心她',
    # 					'interaction_details': '素世会称呼爱音为あのんちゃん'},
    # 	'character_1': {'talk_style': '',
    # 					'interaction_details': '爱音会称呼素世为そよりん（素世世）'},
    # 	'situation': '傍晚乐队练习结束，两人一起走在回家的路上。'}
    #
    # text_queue=Queue()
    # #is_text_generating_queue=Queue()
    # qt2dp_queue=Queue()
    # message_queue=Queue()
    # input("运行")
    # qt2dp_queue.put(user_input)
    # ds_local_and_voice_gen.text_generator(
    # 	text_queue,
    # 	is_text_generating_queue,
    # 	qt2dp_queue,
    # 	message_queue
    # )
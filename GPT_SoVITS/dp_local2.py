import re
import time,os

import requests,json
import litellm
from litellm import completion

from GPT_SoVITS.emotion_enum import EmotionEnum
from character import CharacterManager
from chat.chat import Message, get_chat_manager
from qconfig import d_sakiko_config


class DSLocalAndVoiceGen:
	def __init__(self):
		# with open('../reference_audio/character_description.txt','r',encoding='utf-8') as f:
		# 	cha_describe=f.read()
		self.chat_manager = get_chat_manager()
		self.character_manager = CharacterManager()

		self.audio_language = ["中英混合", "日英混合"]
		self.audio_language_choice = self.audio_language[1]
		self.is_think_content_output_complete=False
		self.model=__import__('live2d_1').get_live2d()
		self.sakiko_state=True
		self.if_generate_audio=True

		# 设置当前正在和我们对话的角色
		self.current_character = self.character_manager.character_class_list[0]
		self.current_chat = self.chat_manager.chat_list[0]
		# 尝试根据角色名称寻找到对应的第一个聊天记录
		for one_chat in self.chat_manager.chat_list:
			if self.current_character.character_name in one_chat.involved_characters:
				self.current_chat = one_chat
				break

		# 设置用户的角色
		self.user_character = self.character_manager.user_characters[0]
		# 尝试根据聊天记录中的角色名称寻找用户角色
		for one_character in self.character_manager.user_characters:
			if one_character.character_name in self.current_chat.involved_characters:
				self.user_character = one_character
				break

		if "祥子" in self.current_chat.involved_characters:
			self.if_sakiko = True
		else:
			self.if_sakiko = False

	def change_character(self):
		current_character_index = self.character_manager.character_class_list.index(self.current_character)
		next_character_index = (current_character_index + 1) % len(self.character_manager.character_class_list)

		self.current_character = self.character_manager.character_class_list[next_character_index]
		self.current_chat = self.chat_manager.chat_list[next_character_index]

		if "祥子" in self.current_chat.involved_characters:
			self.if_sakiko = True
		else:
			self.if_sakiko = False

	def trim_list_to_64kb(self,data_list):
		"""
		将聊天上下文缩短到 64kb 大小
		"""
		MAX_SIZE = 64 * 1024  # 64KB
		while len(json.dumps(data_list, ensure_ascii=False).encode('utf-8')) > MAX_SIZE:
			del data_list[1]

		return data_list
	
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
	
	def report_message_to_main_ui(self, message_queue, is_text_generating_queue, message: str):
		"""
		向主界面的消息栏汇报一条错误信息，并且删除最新正在发给模型的对话记录。
		"""
		message_queue.put(message)
		is_text_generating_queue.get()
		# 删除未能成功发送的信息
		self.current_chat.message_list.pop()
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

			message_queue.put(f"输入bye：退出程序	l：切换语言	m：更改LLM\n"+("conv：切换祥子状态	"if self.if_sakiko else '')+"clr：清空聊天记录	v：关闭/开启语音\n当前语言："+("中文" if self.audio_language_choice=="中英混合" else "日文")+ "		语音："+("开启" if self.if_generate_audio else "关闭")+('	mask...'if self.sakiko_state and self.if_sakiko else ''))
			while True:
				if not qt2dp_queue.empty():
					user_input=qt2dp_queue.get()
					break
				time.sleep(1)
			# --------------------前面的一些判断逻辑

			if user_input == 'bye':
				text_queue.put('bye')
				# print("DeepSeek API 生成模块退出，发送 bye 信号。")
				break

			elif user_input=='mask':
				if self.if_sakiko:
					char_is_converted_queue.put('maskoff')
				else:
					message_queue.put("祥子暂时不在<w>")
				time.sleep(2)
				continue
			elif user_input=='conv':
				if self.if_sakiko:
					self.sakiko_state=not self.sakiko_state
					message_queue.put("已切换为"+("黑祥"if self.sakiko_state else "白祥"))
					char_is_converted_queue.put(self.sakiko_state)
				else:
					message_queue.put("祥子暂时不在<w>")
				time.sleep(2)
				continue
			elif user_input =='v':
				self.if_generate_audio=not self.if_generate_audio
				message_queue.put("已"+("开启" if self.if_generate_audio else "关闭")+"语音合成")
				time.sleep(2)
				continue
			elif user_input=='s':
				self.change_character()
				message_queue.put(f"已切换为：{self.current_character.character_name}\n正在切换GPT-SoVITS模型...")
				change_char_queue.put('yes')
				dp2qt_queue.put("changechange")
				AudioGenerator.change_character()
				time.sleep(2)
				continue
			elif user_input=='clr':
				self.current_chat.clear_message_list()
				message_queue.put("已清空角色的聊天记录")
				time.sleep(2)
				continue
			elif user_input in ['start_talking','stop_talking']:
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
			self.current_chat.add_message(
				Message(
					character_name=self.user_character.character_name,
					text=user_input,
					translation="",
					emotion=0,
					audio_path=""
				)
			)

			print(self.current_chat.build_llm_query(perspective=self.current_character.character_name))

			message_queue.put("小祥思考中..." if self.if_sakiko else f"{self.current_character.character_name}思考中...")
			is_text_generating_queue.put('no_complete')		#正在生成文字的标志
			# --------------------------
			# 优先处理使用 Up API 的情况

			try:
				if d_sakiko_config.use_default_deepseek_api.value:
					response = completion(
						model="deepseek/deepseek-chat",
						messages=self.trim_list_to_64kb(self.current_chat.build_llm_query(perspective=self.current_character)),
						api_key=self.model
					)
				# 第二优先级是检查自定义 API Url
				# 只要存在自定义 API，就使用自定义 API
				elif d_sakiko_config.enable_custom_llm_api_provider.value:
					response = completion(
						model=d_sakiko_config.custom_llm_api_model.value,
						messages=self.trim_list_to_64kb(self.current_chat.build_llm_query(perspective=self.current_character)),
						api_key=d_sakiko_config.custom_llm_api_key.value,
						# 自定义 API 地址
						base_url=d_sakiko_config.custom_llm_api_url.value
					)
				# 最后：使用选择的预定义 API 提供商
				else:
					response = completion(
						model=self.concat_provider_and_model(d_sakiko_config.llm_api_provider.value, d_sakiko_config.llm_api_model.value[d_sakiko_config.llm_api_provider.value]),
						messages=self.trim_list_to_64kb(self.current_chat.build_llm_query(perspective=self.current_character)),
						api_key=d_sakiko_config.llm_api_key.value[d_sakiko_config.llm_api_provider.value]
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
				code = e.response.status_code
				if code == 402:
					self.report_message_to_main_ui(
						message_queue,
						is_text_generating_queue,
						"账户余额不足，如果正在用up的api，请联系UP充值"
					)
				else:
					self.report_message_to_main_ui(
						message_queue,
						is_text_generating_queue,
						f"未知的请求错误，状态码：{code}。"
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

			response_message = response.choices[0].message.content.strip()
			text_queue.put(response_message)

			self.chat_manager.save()

			# TODO：这里应该加一个逻辑：直到 AudioGenerator 为我们返回生成语言内容前，都不应该继续往下走
			# 因为我们需要在信息中记录语音的路径等信息
			# 现在作为 demo 先这么跑着
			self.current_chat.add_message(Message(
				character_name=self.current_character.character_name,
				text=response_message,
				translation="",
				emotion=EmotionEnum.from_string("0"),
				audio_path=""
			))

			# if self.model_choice==self.LLM_list[0] or self.model_choice==self.LLM_list[1]:	#本地模型以及联机调用r1的情况下，打印思考文本
			# 	think_content = re.search(r'<think>(.*?)</think>', response["message"]["content"], flags=re.DOTALL).group(1)
			# 	print("LLM的思考过程：",end='')
			# 	self.stream_print(think_content)
			# elif self.model_choice==self.LLM_list[3]:
			# 	think_content=response['message']['reasoning_content']
			# 	print("LLM的思考过程：", end='')
			# 	self.stream_print(think_content)
			# else:
			# 	pass

			while is_audio_play_complete.get() is None:
				time.sleep(0.5)		#不加就无法正常运行

		# print("DeepSeek API 生成模块已退出完成")

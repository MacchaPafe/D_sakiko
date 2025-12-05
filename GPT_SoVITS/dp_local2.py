import re
import time,os
from ollama import chat
import requests,json
from openai import OpenAI


class DSLocalAndVoiceGen:
	def __init__(self,characters):
		# with open('../reference_audio/character_description.txt','r',encoding='utf-8') as f:
		# 	cha_describe=f.read()
		self.character_list=characters

		with open("../API_Choice.json", "r", encoding="utf-8") as f:
			config = json.load(f)
		self.is_deepseek=True
		for provider in config["llm_choose"]:
			if provider["if_choose"]:
				active_provider = provider
				self.is_deepseek=False
				break
		if not self.is_deepseek:
			if active_provider['name']=="OpenAI":
				self.other_client=OpenAI(
					api_key=active_provider['api_key']
				)
				print("已使用OpenAI API")
			elif active_provider['name']=="Google":
				self.other_client=OpenAI(
					api_key=active_provider['api_key'],
					base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
				)
				print("已使用Google Gemini API")
			self.model_choice=active_provider['model']



		self.all_character_msg=[]
		self.LLM_list=["deepseek-r1:14b","deepseek-r1:32b","deepseek-V3 非本地API","deepseek-R1 非本地API（暂时不能用）"]
		if self.is_deepseek:
			self.model_choice=self.LLM_list[2]


		self.audio_language = ["中英混合", "日英混合"]
		self.audio_language_choice = self.audio_language[1]
		self.is_think_content_output_complete=False
		self.is_use_ds_api=True
		self.model=__import__('live2d_1').get_live2d()
		self.headers = {"Content-Type": "application/json","Authorization": f"Bearer {self.model}"}
		self.sakiko_state=True
		self.if_generate_audio=True
		self.current_char_index=0
		self.if_sakiko=False
		self.initial()

	# def stream_print(self,text):		#引入PyQT后作废
	# 	for char in text:
	# 		print(char, end='', flush=True)
	# 		time.sleep(0.07)  # 每个字间隔70毫秒，模拟流式打印
	# 	print()
	# 	time.sleep(1)

	def initial(self):
		for character in self.character_list:
			self.all_character_msg.append([{"role": "user",
											"content": f'{character.character_description}'}])
		if os.path.getsize('../reference_audio/history_messages_dp.json')!=0:
			with open('../reference_audio/history_messages_dp.json','r',encoding='utf-8') as f:
				json_data=json.load(f)
			for index, character in enumerate(self.character_list):
				for data in json_data:
					if data['character'] == character.character_name:
						self.all_character_msg[index] = data['history']


		if self.character_list[self.current_char_index].character_name == '祥子':
			self.if_sakiko = True
		else:
			self.if_sakiko = False

		if os.path.getsize('../API Key.txt')!=0:
			with open('../API Key.txt','r',encoding='utf-8') as f:
				self.headers={"Content-Type": "application/json","Authorization": f"Bearer {f.read()}"}


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

	def trim_list_to_64kb(self,data_list):
		MAX_SIZE = 64 * 1024  # 64KB
		while len(json.dumps(data_list, ensure_ascii=False).encode('utf-8')) > MAX_SIZE:
			del data_list[1]

		return data_list

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

			if user_input=='mask':
				if self.if_sakiko:
					char_is_converted_queue.put('maskoff')
				else:
					message_queue.put("祥子暂时不在<w>")
				time.sleep(2)
				continue

			if user_input=='conv':
				if self.if_sakiko:
					self.sakiko_state=not self.sakiko_state
					message_queue.put("已切换为"+("黑祥"if self.sakiko_state else "白祥"))
					char_is_converted_queue.put(self.sakiko_state)
				else:
					message_queue.put("祥子暂时不在<w>")
				time.sleep(2)
				continue
			if user_input =='v':
				self.if_generate_audio=not self.if_generate_audio
				message_queue.put("已"+("开启" if self.if_generate_audio else "关闭")+"语音合成")
				time.sleep(2)
				continue
			if user_input=='s':
				self.change_character()
				message_queue.put(f"已切换为：{self.character_list[self.current_char_index].character_name}\n正在切换GPT-SoVITS模型...")
				change_char_queue.put('yes')
				dp2qt_queue.put("changechange")
				AudioGenerator.change_character()
				time.sleep(2)
				continue
			if user_input in ['start_talking','stop_talking']:
				change_char_queue.put(user_input)
				continue


			while True:
				if user_input == 'm':
					if self.is_deepseek:
						message_queue.put("0：deepseek-r1:14b（需安装Ollama与对应本地大模型，选项1相同）  1：deepseek-r1:32b  \n2：调用deepseek-V3官方API（无需安装Ollama，只需联网)")
					else:
						message_queue.put("已使用非Deepseek大模型，暂不支持切换其他模型")
						time.sleep(2)
						break
					while True:
						if not qt2dp_queue.empty():
							user_wants_model = qt2dp_queue.get()
							break
						time.sleep(0.5)
				else:
					break
				if user_wants_model.isdigit() and 0 <= int(user_wants_model) <= 1:
					self.is_use_ds_api=False
					self.model_choice = self.LLM_list[int(user_wants_model)]
					break
				elif user_wants_model.isdigit() and 2<=int(user_wants_model)<=3:
					self.is_use_ds_api=True
					self.model_choice = self.LLM_list[2]
					break
				else:
					message_queue.put("输入内容不合法，重新输入")
					time.sleep(2)
					continue

			if user_input == 'l':
				self.audio_language_choice = "中英混合" if self.audio_language_choice == '日英混合' else '日英混合'
				message_queue.put("已切换语言为："+("中文" if self.audio_language_choice=='中英混合' else "日文"))
				time.sleep(2)
				continue
			if user_input == 'm':
				if self.is_deepseek:
					message_queue.put(f"已修改LLM为：{self.model_choice}")
					time.sleep(2)
					continue
				else:
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
			message_queue.put("小祥思考中..." if self.if_sakiko else f"{self.character_list[self.current_char_index].character_name}思考中...")
			is_text_generating_queue.put('no_complete')		#正在生成文字的标志
			# --------------------------
			if not self.is_use_ds_api and self.is_deepseek:		#使用本地Ollama模型
				try:
					response = chat(
						model=self.model_choice,
						messages=self.trim_list_to_64kb(self.all_character_msg[self.current_char_index]),
						stream=False
					)
				except Exception:
					message_queue.put("本地Ollama API调用出错！输入bye退出并重新启动程序，或使用联网deepseek模式。")
					is_text_generating_queue.get()	#不加这行，出错后模型会一直保持思考状态
					self.all_character_msg[self.current_char_index].pop()
					time.sleep(2)
					continue
			elif self.is_use_ds_api and self.is_deepseek:
				online_model=''
				if self.model_choice==self.LLM_list[2]:
					online_model='deepseek-chat'
				elif self.model_choice==self.LLM_list[3]:
					online_model='deepseek-reasoner'
				data = {
					"model": online_model,
					"messages": self.trim_list_to_64kb(self.all_character_msg[self.current_char_index]),
					"stream": False
				}
				try:

					response = requests.post("https://api.deepseek.com/chat/completions", headers=self.headers, json=data)
				except Exception:
					message_queue.put("与DeepSeek建立连接失败，请检查网络。")
					is_text_generating_queue.get()  # 不加这行，出错后模型会一直保持思考状态
					self.all_character_msg[self.current_char_index].pop()
					time.sleep(2)
					continue
				if response.status_code == 200:

					response=response.json()
					response=response['choices'][0]
				else:
					time.sleep(2)
					if response.status_code==402:
						message_queue.put("deepseek账户余额不足，请联系UP充值")
					elif response.status_code==401:
						message_queue.put("deepseek API key认证出错，请检查正确性")
					elif response.status_code==429:
						message_queue.put("请求速度太快，被限制了（应该不会出现这个错误吧，")
					elif response.status_code==500 or response.status_code==503:
						message_queue.put("deepseek的服务器可能崩了，可等待一会后重试。")
					else:
						message_queue.put("出现了未知错误，可尝试重新对话一次。")
					is_text_generating_queue.get()
					self.all_character_msg[self.current_char_index].pop()
					time.sleep(2)
					continue
			elif not self.is_deepseek:		#使用非Deepseek的模型
				try:
					response = self.other_client.chat.completions.create(
						model=self.model_choice,
						messages=self.all_character_msg[self.current_char_index],
						stream=False
					)
				except Exception as err:
					message_queue.put("模型API调用出错...请检查网络，然后重试一下吧")
					print("模型API调用出错：", err)
					is_text_generating_queue.get()
					self.all_character_msg[self.current_char_index].pop()
					time.sleep(2)
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

			# 去掉多余的字符，使用正则表达式
			if self.is_deepseek:
				cleaned_text_of_model_response = re.sub(r'<think>.*?</think>', '', response["message"]["content"],flags=re.DOTALL).strip()
				model_this_turn_msg = {"role": "assistant", "content": response["message"]["content"]}
			else:
				cleaned_text_of_model_response = re.sub(r'<think>.*?</think>', '', response.choices[0].message.content.strip(), flags=re.DOTALL).strip()
				model_this_turn_msg = {"role": "assistant", "content": response.choices[0].message.content.strip()}
			#print("aaaaaaaaaaaaaaaa",cleaned_text_of_model_response,'bbbbbbbbbbbbbbbbbb')
			text_queue.put(cleaned_text_of_model_response)

			self.all_character_msg[self.current_char_index].append(model_this_turn_msg)

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

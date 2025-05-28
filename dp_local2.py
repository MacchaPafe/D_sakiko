import re
import time
from ollama import chat
import requests



class DSLocalAndVoiceGen:
	def __init__(self):
		with open('../reference_audio/character_description.txt','r',encoding='utf-8') as f:
			cha_describe=f.read()
		self.history_msg_list=[{"role":"user",
				   				"content":f'{cha_describe}'}]

		self.LLM_list=["deepseek-r1:14b","deepseek-r1:32b","deepseek-V3 非本地API","deepseek-R1 非本地API（暂时不能用）"]
		self.model_choice=self.LLM_list[2]
		self.audio_language = ["中英混合", "日英混合"]
		self.audio_language_choice = self.audio_language[0]
		self.is_think_content_output_complete=False
		self.is_use_ds_api=True
		self.model=__import__('live2d_1').get_live2d()
		self.headers = {"Content-Type": "application/json","Authorization": f"Bearer {self.model}"}
		self.sakiko_state=True

	'''def stream_print(self,text):		#引入PyQT后作废
		for char in text:
			print(char, end='', flush=True)
			time.sleep(0.07)  # 每个字间隔70毫秒，模拟流式打印
		print()
		time.sleep(1)'''


	def text_generator(self,text_queue,is_audio_play_complete,is_text_generating_queue,dp2qt_queue,qt2dp_queue,message_queue,char_is_converted_queue):

		while True:
			#user_input = input(">>>输入bye退出聊天，输入“lan”更改语音语言，输入“model”更改LLM\n"
							   #+ f">>>当前模型：{self.model_choice}  语音语言：{self.audio_language_choice}\n>>>")
			message_queue.put(f"输入bye退出程序 “lan”更改语音语言 “model”更改LLM \n“conv”切换祥子形态\n当前语音语言：{self.audio_language_choice}  “clr”清屏")
			while True:
				if not qt2dp_queue.empty():
					user_input=qt2dp_queue.get()
					break
				time.sleep(1)
			# --------------------前面的一些判断逻辑

			if user_input == 'bye':
				text_queue.put('bye')
				break

			if user_input=='conv':
				self.sakiko_state=not self.sakiko_state
				message_queue.put("已切换为"+("黑祥"if self.sakiko_state else "白祥"))
				char_is_converted_queue.put(self.sakiko_state)
				time.sleep(2)
				continue
			'''if user_input =='cls':
				os.system('cls')
				print("输入bye退出聊天，输入“lan”更改语音语言，输入“cls“清屏")
				continue'''
			while True:
				if user_input == "lan":
					message_queue.put("0：中英混合  1：日英混合\n更改后会自动切换为对应语言的语音")
					while True:
						if not qt2dp_queue.empty():
							user_wants_language = qt2dp_queue.get()
							break
						time.sleep(0.5)
				else:
					break
				if user_wants_language.isdigit() and 0 <= int(user_wants_language) <= 1:
					self.audio_language_choice = self.audio_language[int(user_wants_language)]
					break
				else:
					message_queue.put("输入内容不合法，重新输入")
					time.sleep(2)
					continue

			while True:
				if user_input == 'model':
					message_queue.put("0：deepseek-r1:14b（需安装Ollama与对应本地大模型，选项1相同）  1：deepseek-r1:32b  \n2：调用deepseek-V3官方API（无需安装Ollama，只需联网)")
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

			if user_input == 'lan':
				message_queue.put(f"已修改语音语言为：{self.audio_language_choice}")
				time.sleep(2)
				continue
			if user_input == 'model':
				message_queue.put(f"已修改LLM为：{self.model_choice}")
				time.sleep(2)
				continue
			if self.audio_language_choice=='日英混合':
				user_input=user_input+'（本句话用日语回答）'
			else:
				user_input = user_input + '（本句话用中文回答）'
			if self.sakiko_state:
				user_input = user_input +'（本句话用黑祥语气回答!）'
			else:
				user_input = user_input + '（本句话用白祥语气回答!）'
			# -----------------------------
			user_this_turn_msg = {"role": "user",
								  "content": user_input}
			self.history_msg_list.append(user_this_turn_msg)
			message_queue.put("小祥思考中...")
			is_text_generating_queue.put('no_complete')		#正在生成文字的标志
			# --------------------------
			if not self.is_use_ds_api:
				try:
					response = chat(
						model=self.model_choice,
						messages=self.history_msg_list,
						stream=False
					)
				except Exception:
					message_queue.put("本地Ollama API调用出错！输入bye退出并重新启动程序，或使用联网deepseek模式。")
					is_text_generating_queue.get()	#不加这行，出错后模型会一直保持思考状态
					self.history_msg_list.pop()
					time.sleep(2)
					continue
			else:
				online_model=''
				if self.model_choice==self.LLM_list[2]:
					online_model='deepseek-chat'
				elif self.model_choice==self.LLM_list[3]:
					online_model='deepseek-reasoner'
				data = {
					"model": online_model,
					"messages": self.history_msg_list,
					"stream": False
				}
				try:
					response = requests.post("https://api.deepseek.com/chat/completions", headers=self.headers, json=data)
				except Exception:
					message_queue.put("与DeepSeek建立连接失败，请检查网络。")
					is_text_generating_queue.get()  # 不加这行，出错后模型会一直保持思考状态
					self.history_msg_list.pop()
					time.sleep(2)
					continue
				if response.status_code == 200:
					response=response.json()
					response=response['choices'][0]
				else:
					time.sleep(2)
					if response.status_code==402:
						message_queue.put("deepseek账户余额不足。本次对话未能成功。")
					elif response.status_code==401:
						message_queue.put("deepseek API key认证出错")
					elif response.status_code==429:
						message_queue.put("请求速度太快，被限制了（应该不会出现这个错误吧，")
					elif response.status_code==500 or response.status_code==503:
						message_queue.put("deepseek的服务器可能崩了，可等待一会后重试。")
					else:
						message_queue.put("出现了未知错误，可尝试重新对话一次。")
					is_text_generating_queue.get()
					self.history_msg_list.pop()
					time.sleep(2)
					continue
			# 去掉多余的字符，使用正则表达式
			cleaned_text_of_model_response = re.sub(r'<think>.*?</think>', '', response["message"]["content"],flags=re.DOTALL).strip()

			text_queue.put(cleaned_text_of_model_response)
			model_this_turn_msg = {"role": "assistant", "content": response["message"]["content"]}
			self.history_msg_list.append(model_this_turn_msg)

			'''if self.model_choice==self.LLM_list[0] or self.model_choice==self.LLM_list[1]:	#本地模型以及联机调用r1的情况下，打印思考文本
				think_content = re.search(r'<think>(.*?)</think>', response["message"]["content"], flags=re.DOTALL).group(1)
				print("LLM的思考过程：",end='')
				self.stream_print(think_content)
			elif self.model_choice==self.LLM_list[3]:
				think_content=response['message']['reasoning_content']
				print("LLM的思考过程：", end='')
				self.stream_print(think_content)
			else:
				pass'''

			while is_audio_play_complete.get() is None:
				time.sleep(1)		#不加这个就无法正常运行

import time,os

from ollama import chat
import requests,json
from openai import OpenAI


class DSLocalAndVoiceGen:
	def __init__(self,characters):
		self.character_list=characters
		self.current_character_num=[0,1]
		if not os.path.exists("../dsakiko_config.json"):
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
					print("已使用自建OpenAI API")
				elif active_provider['name']=="Google":
					self.other_client=OpenAI(
						api_key=active_provider['api_key'],
						base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
					)
					print("已使用自建Google Gemini API")
				self.model_choice=active_provider['model']
		else:
			with (open('../dsakiko_config.json', 'r', encoding='utf-8') as f):
				config = json.load(f)
				llm_config = config["llm_setting"]
			self.is_deepseek = llm_config["is_deepseek"]
			if not self.is_deepseek:
				for provider in llm_config["other_provider"]:
					if provider["if_choose"]:
						active_provider = provider
						break
				try:
					self.other_client = OpenAI(
						api_key=active_provider['api_key'],
						base_url=active_provider["base_url"]
					)
					print(f"已使用个人{active_provider['name']} API")
					self.model_choice = active_provider['model']
				except Exception as err:
					raise Warning(
						f"dsakiko_config.json配置文件有误，重新运行一遍启动配置程序应该可以解决问题。错误信息：", err)

		#self.all_character_msg=[]

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
		#self.current_char_index=0
		self.if_sakiko=False
		self.base_prompt = '''
								# Role
								你是一位精通心理学和戏剧创作的资深编剧，擅长创作符合ACG角色设定的沉浸式对话（小剧场）。

								# Goal
								你需要根据用户提供的两位角色的详细设定，编写一段两人之间的对话。

								# Rules
								1. **性格沉浸**：严格遵守两人的说话习惯、心理状态。
								2. **关系体现**：对话要体现两人过去的历史纠葛和当前的某种关系（如：尴尬、亲密、敌对、表面客气但内心疏离等）。
								3. **格式严格**：必须只返回一个 JSON 列表，不要包含任何 Markdown 标记或其他废话。
								
								# Content Safety
								This is a wholesome, all-ages comedy scenario. Avoid any sexual, violent, or non-consensual content. Focus on the humor of the situation.
								
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
		if not os.path.exists('../dsakiko_config.json'):
			if self.is_deepseek:
				if os.path.getsize('../API Key.txt') != 0:
					print("已使用个人DeepSeek API")
					with open('../API Key.txt', 'r', encoding='utf-8') as f:
						self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {f.read()}"}
				else:
					# print("正在使用Up的DeepSeek API")
					pass
		else:
			if self.is_deepseek:
				with open('../dsakiko_config.json', 'r', encoding='utf-8') as f:
					config = json.load(f)
				if config["llm_setting"]["deepseek_key"] in ['', 'use_api_of_up']:
					pass
				else:
					print("已使用个人DeepSeek API")
					self.headers = {"Content-Type": "application/json",
									"Authorization": f"Bearer {config['llm_setting']['deepseek_key']}"}

	def text_generator(self,
					   text_queue,
					   qt2dp_queue,
					   message_queue,
					   ):

		while True:
			while True:
				if not qt2dp_queue.empty():
					data=qt2dp_queue.get()
					if data=='EXIT':
						user_input='EXIT'
						break
					self.current_character_num=data['char_index']
					user_input=data['user_input']
					if not user_input:
						user_input={'character_0':{'talk_style':'','interaction_details':''},
									'character_1':{'talk_style':'','interaction_details':''},
									'situation':''}
					break
				time.sleep(0.2)
			if user_input=='EXIT':
				break

			user_prompt=f'''
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

			user_this_turn_msg = [{"role":"system","content":self.base_prompt},
								  {"role": "user","content": user_prompt}]
			message_queue.put("调用大模型生成文本中...")
			time.sleep(2)
			test_text='''
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
			if not self.is_use_ds_api and self.is_deepseek:		#使用本地Ollama模型
				try:
					response = chat(
						model=self.model_choice,
						messages=user_this_turn_msg,
						stream=False
					)
				except Exception:
					message_queue.put("本地Ollama API调用出错！输入bye退出并重新启动程序，或使用联网deepseek模式。")
					text_queue.put("error")
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
					"messages": user_this_turn_msg,
					"stream": False
				}
				try:

					response = requests.post("https://api.deepseek.com/chat/completions", headers=self.headers, json=data)
				except Exception:
					message_queue.put("与DeepSeek建立连接失败，请检查网络。")
					text_queue.put("error")
					time.sleep(2)
					continue
				if response.status_code == 200:

					response=response.json()
					response=response['choices'][0]
				else:
					time.sleep(2)
					print("DeepSeek API调用出错，错误代码：", response.status_code)
					if response.status_code==402:
						print("账户余额不足，如果正在用up的api，请联系UP充值")
					elif response.status_code==401:
						print("deepseek API key认证出错，请检查正确性")
					elif response.status_code==429:
						print("请求速度太快，被限制了（应该不会出现这个错误吧，")
					elif response.status_code==500 or response.status_code==503:
						print("deepseek服务器可能崩了，可等待一会后重试。")
					else:
						print("出现了未知错误，可尝试重新对话一次。")
					text_queue.put("error")

					time.sleep(2)
					continue
			elif not self.is_deepseek:		#使用非Deepseek的模型
				try:
					response = self.other_client.chat.completions.create(
						model=self.model_choice,
						messages=user_this_turn_msg,
						stream=False,
						timeout=100
					)
					if response.choices[0].message.content is None:
						message_queue.put("模型API返回内容为空，请检查网络，然后重试一下吧")
						print("模型API返回内容为空!")
						text_queue.put("error")
						time.sleep(2)
						continue
				except Exception as err:
					message_queue.put("模型API调用出错...请检查网络，然后重试一下吧")
					print("模型API调用出错：", err)
					text_queue.put("error")
					time.sleep(2)
					continue

			# 去掉多余的字符，使用正则表达式
			if self.is_deepseek:
				response_json=response["message"]["content"]
			else:
				response_json=response.choices[0].message.content.strip()
			text_queue.put(response_json)

if __name__ == "__main__":
	import sys

	script_dir = os.path.dirname(os.path.abspath(__file__))
	sys.path.insert(0, script_dir)
	import character
	from queue import Queue

	get_all = character.GetCharacterAttributes()
	characters = get_all.character_class_list
	ds_local_and_voice_gen=DSLocalAndVoiceGen(characters)
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

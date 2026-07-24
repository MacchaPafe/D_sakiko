const nowSeconds = () => Math.floor(Date.now() / 1000)

export const MOCK_CHARACTERS = {
  anon: {
    id: 'anon',
    name: '爱音',
    avatar_url: '/mock-assets/avatars/爱音.png',
    model_url: '/mock-assets/live2d/anon/3.model.json',
    accent: '#168779',
    accent_soft: '#dcefeb',
    audio_url: '/mock-assets/audio/anon-reference.wav',
  },
  arisa: {
    id: 'arisa',
    name: '有咲',
    avatar_url: '/mock-assets/avatars/有咲.png',
    model_url: '/mock-assets/live2d/arisa/3.model.json',
    accent: '#c24f67',
    accent_soft: '#f7e2e7',
    audio_url: '/mock-assets/audio/anon-reference.wav',
  },
  sakiko: {
    id: 'sakiko',
    name: '祥子',
    avatar_url: '/mock-assets/avatars/祥子.png',
    model_url: '/mock-assets/live2d/sakiko/3.model.json',
    accent: '#486fa8',
    accent_soft: '#dfe8f5',
    audio_url: '/mock-assets/audio/sakiko-white.wav',
  },
}

export const MOCK_BACKGROUNDS = [
  {
    id: 'school',
    name: '校舍',
    image_url: '/mock-assets/backgrounds/bg00474.png',
    color: '#cfd9dc',
  },
  {
    id: 'quiet',
    name: '安静',
    image_url: '',
    color: '#cbd8d4',
  },
]

const message = ({
  id,
  role,
  text,
  translation = '',
  minutesAgo,
  turnId = '',
  sequence = 0,
  emotion = 'neutral',
  audioUrl = '',
}) => ({
  id,
  role,
  text,
  translation,
  created_at: nowSeconds() - minutesAgo * 60,
  turn_id: turnId,
  sequence,
  emotion,
  audio_url: audioUrl,
  status: 'ready',
})

export function createInitialMockChats() {
  return [
    {
      chat_id: 'chat_anon_daily',
      name: '放学后的闲聊',
      character_id: 'anon',
      last_active_at: nowSeconds() - 4 * 60,
      messages: [
        message({
          id: 'anon-user-1',
          role: 'user',
          text: '今天练习怎么样？',
          minutesAgo: 7,
        }),
        message({
          id: 'anon-assistant-1',
          role: 'assistant',
          text: '今日はかなりいい感じだったよ。',
          translation: '今天状态相当不错哦。',
          minutesAgo: 6,
          turnId: 'turn-anon-history',
          sequence: 0,
          emotion: 'happiness',
          audioUrl: MOCK_CHARACTERS.anon.audio_url,
        }),
        message({
          id: 'anon-assistant-2',
          role: 'assistant',
          text: 'でも、最後のところはもう少し合わせたいかな。',
          translation: '不过最后那一段，我还想再配合得更好一点。',
          minutesAgo: 5,
          turnId: 'turn-anon-history',
          sequence: 1,
          emotion: 'neutral',
          audioUrl: MOCK_CHARACTERS.anon.audio_url,
        }),
      ],
    },
    {
      chat_id: 'chat_arisa_study',
      name: '概念学习',
      character_id: 'arisa',
      last_active_at: nowSeconds() - 47 * 60,
      messages: [
        message({
          id: 'arisa-user-1',
          role: 'user',
          text: '可以用简单的话解释一下异步吗？',
          minutesAgo: 52,
        }),
        message({
          id: 'arisa-assistant-1',
          role: 'assistant',
          text: 'まずは「待っている間に別のことを進める仕組み」だと思えばいいわ。',
          translation: '先把它理解成“等待期间可以继续做别的事”的机制就好。',
          minutesAgo: 50,
          turnId: 'turn-arisa-history',
          emotion: 'neutral',
          audioUrl: MOCK_CHARACTERS.arisa.audio_url,
        }),
      ],
    },
    {
      chat_id: 'chat_anon_band',
      name: '乐队练习计划',
      character_id: 'anon',
      last_active_at: nowSeconds() - 3 * 60 * 60,
      messages: [
        message({
          id: 'anon-band-user-1',
          role: 'user',
          text: '下次练习要先排哪首歌？',
          minutesAgo: 184,
        }),
        message({
          id: 'anon-band-assistant-1',
          role: 'assistant',
          text: '先把新曲的副歌合一遍吧！',
          translation: '',
          minutesAgo: 181,
          turnId: 'turn-anon-band-history',
          emotion: 'happiness',
          audioUrl: MOCK_CHARACTERS.anon.audio_url,
        }),
      ],
    },
    {
      chat_id: 'chat_sakiko_music',
      name: '作曲讨论',
      character_id: 'sakiko',
      last_active_at: nowSeconds() - 26 * 60 * 60,
      messages: [
        message({
          id: 'sakiko-user-1',
          role: 'user',
          text: '这段旋律是不是应该更克制一点？',
          minutesAgo: 1580,
        }),
        message({
          id: 'sakiko-assistant-1',
          role: 'assistant',
          text: 'ええ。ここは感情を見せすぎないほうが、次の展開が映えますわ。',
          translation: '是的。这里不把情绪表现得太满，后面的展开会更突出。',
          minutesAgo: 1570,
          turnId: 'turn-sakiko-history',
          emotion: 'neutral',
          audioUrl: MOCK_CHARACTERS.sakiko.audio_url,
        }),
      ],
    },
  ]
}

export function mockResponseFor(characterId) {
  const responses = {
    anon: [
      {
        text: 'うん、ちゃんと聞いてるよ。',
        translation: '嗯，我有认真听。',
        emotion: 'happiness',
      },
      {
        text: 'まず一番気になるところから、一緒に整理してみようか。',
        translation: '先从最在意的地方开始，一起整理一下吧。',
        emotion: 'neutral',
      },
      {
        text: '急がなくても大丈夫だからね。',
        translation: '不用着急也没关系。',
        emotion: 'happiness',
      },
    ],
    arisa: [
      {
        text: 'もう、いきなり難しく考えすぎなのよ。',
        translation: '真是的，你一开始就想得太复杂了。',
        emotion: 'anger',
      },
      {
        text: '前提と結論を分ければ、ずっと分かりやすくなるわ。',
        translation: '把前提和结论分开，就会容易理解得多。',
        emotion: 'neutral',
      },
    ],
    sakiko: [
      {
        text: '順序立てて考えれば、答えはそれほど遠くありませんわ。',
        translation: '只要按顺序思考，答案并没有那么遥远。',
        emotion: 'neutral',
      },
      {
        text: 'まず、あなたが何を確かめたいのかを明確にしましょう。',
        translation: '先明确你究竟想确认什么吧。',
        emotion: 'neutral',
      },
    ],
  }
  return responses[characterId] || responses.anon
}

export function cloneMockValue(value) {
  return JSON.parse(JSON.stringify(value))
}

/**
 * @typedef {object} ProviderPreset
 * @property {string} id 连接预设身份，不代表独立 SDK 包。
 * @property {string} name 显示名称。
 * @property {'chat-completions' | 'responses' | 'anthropic-messages' | 'google-generative-ai'} protocol 通信协议。
 * @property {string} baseUrl 默认服务地址；空值表示必须自行填写。
 */

/** @type {ProviderPreset[]} 常用厂商共享协议实现，模型能力由连接中的显式声明决定。 */
export const providerPresets = [
  {
    id: 'deepseek',
    name: 'DeepSeek',
    protocol: 'chat-completions',
    baseUrl: 'https://api.deepseek.com/v1'
  },
  {
    id: 'volcengine',
    name: '火山引擎',
    protocol: 'chat-completions',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3'
  },
  {
    id: 'moonshot',
    name: 'Kimi',
    protocol: 'chat-completions',
    baseUrl: 'https://api.moonshot.cn/v1'
  },
  {
    id: 'openai',
    name: 'OpenAI',
    protocol: 'chat-completions',
    baseUrl: 'https://api.openai.com/v1'
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    protocol: 'anthropic-messages',
    baseUrl: 'https://api.anthropic.com/v1'
  },
  {
    id: 'gemini',
    name: 'Google Gemini',
    protocol: 'google-generative-ai',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta'
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    protocol: 'chat-completions',
    baseUrl: 'https://openrouter.ai/api/v1'
  },
  { id: 'vllm', name: 'vLLM', protocol: 'chat-completions', baseUrl: 'http://localhost:8000/v1' },
  {
    id: 'ollama',
    name: 'Ollama',
    protocol: 'chat-completions',
    baseUrl: 'http://localhost:11434/v1'
  },
  { id: 'xai', name: 'Grok', protocol: 'chat-completions', baseUrl: 'https://api.x.ai/v1' },
  {
    id: 'modelscope',
    name: '魔搭社区',
    protocol: 'chat-completions',
    baseUrl: 'https://api-inference.modelscope.cn/v1'
  },
  { id: 'azure', name: 'Azure（兼容端点）', protocol: 'chat-completions', baseUrl: '' },
  {
    id: 'huggingface',
    name: 'Hugging Face（兼容端点）',
    protocol: 'chat-completions',
    baseUrl: 'https://router.huggingface.co/v1'
  },
  {
    id: 'openai-compatible',
    name: 'OpenAI Completions 兼容',
    protocol: 'chat-completions',
    baseUrl: ''
  },
  { id: 'responses-compatible', name: 'OpenAI Responses 兼容', protocol: 'responses', baseUrl: '' },
  {
    id: 'anthropic-compatible',
    name: 'Anthropic Messages 兼容',
    protocol: 'anthropic-messages',
    baseUrl: ''
  }
]

import { defineConfig } from 'vitepress'

export default defineConfig({
  title: '数字小祥帮助文档',
  description: '数字小祥的功能说明、常见问题与反馈入口',

  // 如果访问地址是 https://用户名.github.io/仓库名/，这里填 '/仓库名/'
  // 如果以后绑定独立域名，比如 docs.xxx.com，就改成 '/'
  base: '/D_sakiko/',

  themeConfig: {
    search: {
      provider: 'local'
    },
    nav: [
      { text: '快速开始', link: '/guide/start' },
      { text: '常见问题', link: '/faq' },
      { text: '反馈', link: '/feedback' }
    ],
    sidebar: [
      {
        text: '使用指南',
        items: [
          { text: '快速开始', link: '/guide/start' },
          { text: '更新说明', link: '/guide/update' },
          { text: '大模型配置', link: '/guide/llm' },
          { text: 'Live2D', link: '/guide/live2d' }
        ]
      },
      {
        text: '支持',
        items: [
          { text: '常见问题', link: '/faq' },
          { text: '问题反馈', link: '/feedback' }
        ]
      }
    ]
  }
})

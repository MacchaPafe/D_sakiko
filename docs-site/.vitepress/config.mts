import { defineConfig } from 'vitepress'

export default defineConfig({
  title: '数字小祥文档',
  description: '数字小祥的功能说明、常见问题与反馈入口',

  // 如果访问地址是 https://用户名.github.io/仓库名/，这里填 '/仓库名/'
  // 如果以后绑定独立域名，比如 docs.xxx.com，就改成 '/'
  base: '/',

  themeConfig: {
    search: {
      provider: 'local'
    },
    nav: [
      { text: '使用指南', link: '/guide/start' },
      { text: '开发文档', link: '/dev/setup' },
    ],
    sidebar: {
      '/guide/': [
        {
          text: '开始使用',
          collapsed: false,
          items: [
            { text: '开始使用', link: '/guide/start' },
            { text: '运行要求', link: '/guide/getting-started/requirements' },
            { text: 'Windows 安装', link: '/guide/getting-started/windows' },
            { text: 'macOS 安装', link: '/guide/getting-started/macos' },
            { text: '第一次启动', link: '/guide/getting-started/first-run' },
            { text: '界面与基本操作', link: '/guide/getting-started/interface' },
          ]
        },
        {
          text: '使用指南',
          items: [
            { text: '更新说明', link: '/guide/update' },
            { text: '获取帮助', link: '/guide/feedback' },
          ]
        },
        {
          text: '功能说明',
          items: [
            { text: 'AI 模型设置', link: '/guide/llm' },
            { text: '设置中心与个性化', link: '/guide/settings' },
            { text: 'Live2D相关', link: '/guide/live2d' },
          ]
        }
      ],
      '/dev/': [
        {
          text: '开发文档',
          items: [
            { text: '开发环境设置', link: '/dev/setup' },
          ]
        }
      ]
    }
  }
})

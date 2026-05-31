---
# https://vitepress.dev/reference/default-theme-home-page
layout: home

hero:
  name: "数字小祥"
  tagline: "基于多模态与 ReAct 框架的桌面数字生命 Agent"
  # image:
  #   src: /logo.png # 建议上传一张小祥的头像或应用图标到 public 目录下
  #   alt: 数字小祥
  actions:
    - theme: brand
      text: "快速开始"
      link: /guide/start
    - theme: alt
      text: "常见问题与反馈"
      link: /guide/faq

features:
  - title: "🎙️ 全链路多模态"
    details: "打通 ASR (语音识别) → LLM (思考) → TTS (语音合成) → Live2D (动作渲染) 的完整数据流，带来沉浸式交互体验。"
  - title: "⚙️ 轻量级 ReAct 引擎"
    details: "从零构建的强解耦 Agent 执行流与沙盒机制，自动捕获异常并交由大模型自主反思纠错，无需沉重的外部框架依赖。"
  - title: "🎭 多样化互动系统"
    details: "包含 Live2D 服装自动下载、动作组自定义编辑，以及双角色自动生成对话的“小剧场模式”，玩法丰富。"
  - title: "📦 开箱即用"
    details: "极低门槛的打包方案，支持 Windows 与 MacOS。无需繁琐的环境配置，提供内置大模型 API，双击即可运行。"
---

<style>
/* 可以在这里添加一些针对首页的自定义样式，例如调整视频居中 */
:root {
  --vp-home-hero-name-color: transparent;
  --vp-home-hero-name-background: -webkit-linear-gradient(120deg, #bd34fe, #41d1ff);
}
</style>

<h2 align="center">加入社区</h2>
<p align="center">项目完全免费开源，欢迎进群交流或提供建议</p>

<div align="center">
  <p><strong>QQ交流群：1026822753</strong></p>
  <a href="https://afdian.com/a/MacchaPafe" target="_blank">
    <img src="https://img.shields.io/badge/爱发电-赞助项目-946ce6?style=for-the-badge&logo=aifadian&logoColor=white" alt="赞助项目">
  </a>
</div>

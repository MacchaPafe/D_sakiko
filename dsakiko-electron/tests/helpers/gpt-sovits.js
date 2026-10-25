/** 构造完整启动清单；测试握手时文件仅需存在，不加载推理依赖。 */
export function gptSovitsShared(file) {
  return {
    bert: { config: file, weights: file, tokenizer: file },
    hubert: { config: file, weights: file, featureExtractor: file },
    g2pw: {
      model: file,
      config: file,
      polyphonicCharacters: file,
      monophonicCharacters: file,
      bopomofoToPinyin: file,
      characterBopomofo: file
    },
    languageDetector: { model: file }
  }
}

/** 为宿主测试提供固定设备和资源快照。 */
export function gptSovitsStartup(file) {
  return {
    resources: gptSovitsShared(file),
    architecture: 'v2',
    precision: 'float32',
    device: 'cpu'
  }
}

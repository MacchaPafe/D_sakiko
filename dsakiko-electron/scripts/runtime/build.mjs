import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import { spawn } from 'node:child_process'
import { parseArgs } from 'node:util'
import { createReadStream } from 'node:fs'
import { createHash } from 'node:crypto'
import {
  cp,
  mkdir,
  readFile,
  writeFile,
  readdir,
  rm,
  stat,
  statfs,
  realpath
} from 'node:fs/promises'
import { extractArchive, prepareRuntime } from '../../src/backend/setup/installers.js'
import { runtimePackages } from '../../src/backend/setup/catalog.js'

/**
 * @typedef {object} RuntimeArchive
 * @property {string} path 固定发布文件名。
 * @property {number} sizeBytes 压缩包字节数。
 * @property {number} expandedBytes 解包文件总字节数。
 * @property {string} sha256 实际压缩包的 SHA-256。
 * @property {'zip'} archive 客户端支持的归档格式。
 */

/**
 * @typedef {object} RuntimePackage
 * @property {'asr'|'gsv'} kind 运行环境类别。
 * @property {string} target 平台、架构和 GSV 加速方式。
 * @property {string} version 人工指定的发布版本。
 * @property {1} setupApiVersion 固定安装协议版本。
 * @property {number} installBytes 解包之外的离线安装空间预算。
 * @property {RuntimeArchive[]} files 本组件的归档清单。
 */

const project = fileURLToPath(new URL('../../', import.meta.url))
const script = fileURLToPath(new URL('./materials.py', import.meta.url))
const { values } = parseArgs({
  options: {
    kind: { type: 'string' },
    accelerator: { type: 'string', default: 'cpu' },
    version: { type: 'string', default: '1.0.0' },
    cuda: { type: 'string', default: 'cu126' },
    'skip-gpu-test': { type: 'boolean', default: false },
    'keep-work': { type: 'boolean', default: false },
    'verify-existing': { type: 'boolean', default: false },
    'verify-candidate': { type: 'boolean', default: false },
    'asr-model': { type: 'string' },
    'asr-audio': { type: 'string' },
    'asr-language': { type: 'string' },
    'gsv-config': { type: 'string', multiple: true },
    'nltk-data': { type: 'string' },
    'openjtalk-dict': { type: 'string' },
    help: { type: 'boolean', default: false }
  }
})
const windows = process.platform === 'win32'
const platform = `${process.platform}-${process.arch}`
const work = path.join(project, '.runtime-build')
const output = path.join(project, 'dist', 'runtimes')
const env = {
  ...process.env,
  UV_CACHE_DIR: path.join(work, 'uv-cache'),
  UV_PYTHON_INSTALL_DIR: path.join(work, 'python'),
  UV_PYTHON_DOWNLOADS: 'never',
  PIP_DISABLE_PIP_VERSION_CHECK: '1',
  PIP_CACHE_DIR: path.join(work, 'pip-cache'),
  npm_config_cache: path.join(work, 'npm-cache'),
  PYTHONNOUSERSITE: '1'
}
delete env.PYTHONPATH
delete env.PYTHONHOME
delete env.NODE_PATH
delete env.NODE_OPTIONS

/** 执行固定构建命令，参数独立传递，失败立即停止发布。 */
async function run(executable, args, options = {}) {
  console.log('运行：', path.basename(executable), args.join(' '))
  await new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
      cwd: project,
      stdio: 'inherit',
      ...options,
      env: { ...env, ...options.env }
    })
    child.once('error', reject)
    child.once('exit', (code) =>
      code === 0 ? resolve() : reject(new Error(`命令失败：${executable} (${code})`))
    )
  })
}

/** 在大型步骤前检查剩余空间，保留最低运行余量。 */
async function requireSpace(bytes) {
  const volume = await statfs(work)
  if (volume.bavail * volume.bsize < bytes)
    throw new Error(
      `可用磁盘空间不足，需要至少 ${(bytes / 2 ** 30).toFixed(1)} GiB。构建材料已保留。`
    )
}

/** 只替换脚本拥有的目标目录，防止误清理其他工程文件。 */
async function reset(directory) {
  const relative = path.relative(work, directory)
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative))
    throw new Error('无效的构建清理路径')
  await rm(directory, { recursive: true, force: true })
  await mkdir(directory, { recursive: true })
}

/** npm 的 JavaScript 入口避免 Windows 的 cmd 参数转义差异。 */
async function npmCli() {
  const candidates = [path.join(path.dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js')]
  for (const directory of (process.env.PATH ?? '').split(path.delimiter)) {
    candidates.push(path.join(directory, 'node_modules/npm/bin/npm-cli.js'))
    if (!windows) candidates.push(path.join(directory, 'npm'))
  }
  for (const candidate of candidates) {
    try {
      return await realpath(candidate)
    } catch {
      /* 继续查找当前 Node 的 npm。 */
    }
  }
  throw new Error('找不到 npm CLI，请使用包含 npm 的 Node.js 发行版。')
}

/** 准备固定补丁版本的独立解释器，ASR 的 ZIP 操作也复用此工具。 */
async function pythonTool() {
  await run(
    'uv',
    [
      'python',
      'install',
      '3.11.18',
      '--install-dir',
      env.UV_PYTHON_INSTALL_DIR,
      '--no-bin',
      '--no-registry'
    ],
    { env: { UV_PYTHON_DOWNLOADS: 'automatic' } }
  )
  const name = (await readdir(env.UV_PYTHON_INSTALL_DIR)).find(
    (name) =>
      name.startsWith('cpython-3.11.18-') &&
      name.includes(windows ? 'windows-x86_64' : 'macos-aarch64')
  )
  if (!name) throw new Error('未找到目标平台独立 Python 3.11.18')
  const root = path.join(env.UV_PYTHON_INSTALL_DIR, name)
  return { root, executable: path.join(root, windows ? 'python.exe' : 'bin/python3') }
}

/** 准备 ASR 的锁定依赖，复制为普通文件以符合客户端解包契约。 */
async function prepareAsr(stage) {
  const install = path.join(work, 'asr-install-' + platform)
  await reset(install)
  for (const name of ['package.json', 'package-lock.json'])
    await cp(path.join(project, 'scripts/runtime/asr', name), path.join(install, name))
  await run(process.execPath, [await npmCli(), 'ci', '--omit=dev', '--no-audit', '--no-fund'], {
    cwd: install
  })
  // npm 的 .bin 链接不属于模型推理入口，省去未使用的命令行包装器。
  await rm(path.join(install, 'node_modules/.bin'), { recursive: true, force: true })
  await cp(install, stage, { recursive: true, dereference: true })
  await reset(install)
  const binaries = path.join(stage, 'node_modules/onnxruntime-node/bin/napi-v3')
  for (const name of await readdir(binaries))
    if (name !== process.platform)
      await rm(path.join(binaries, name), { recursive: true, force: true })
  for (const name of await readdir(path.join(binaries, process.platform)))
    if (name !== process.arch)
      await rm(path.join(binaries, process.platform, name), { recursive: true, force: true })
}

/** 构建 GSV 离线材料，平台专属 Torch 只在 Windows 构建时替换。 */
async function prepareGsv(stage, python) {
  await cp(python.root, path.join(stage, 'python'), { recursive: true, dereference: true })
  if (!windows) {
    // uv 会把 libpython 的安装身份改成管理目录绝对路径，发布副本恢复为可搬运身份。
    const library = path.join(stage, 'python/lib/libpython3.11.dylib')
    await run('install_name_tool', ['-id', '@rpath/libpython3.11.dylib', library])
    await run('codesign', ['--force', '--sign', '-', library])
  }
  const tools = path.join(work, 'builder-' + platform)
  await run('uv', ['venv', '--clear', '--seed', '--python', python.executable, tools])
  const builderPython = path.join(tools, windows ? 'Scripts/python.exe' : 'bin/python')
  const requirements = path.join(work, 'requirements-' + platform + '.txt')
  await run('uv', [
    'export',
    '--project',
    path.join(project, 'python'),
    '--frozen',
    '--no-dev',
    '--no-emit-project',
    '--no-hashes',
    '--output-file',
    requirements
  ])
  if (windows) {
    const variant = values.accelerator === 'cuda' ? values.cuda : 'cpu'
    const lines = (await readFile(requirements, 'utf8'))
      .split('\n')
      .filter((line) => !/^(torch|torchaudio)==/.test(line))
    lines.push(`torch==2.7.1+${variant}`, `torchaudio==2.7.1+${variant}`)
    await writeFile(requirements, lines.join('\n'))
  }
  const wheelhouse = path.join(stage, 'wheelhouse')
  await mkdir(wheelhouse, { recursive: true })
  const wheelArgs = [
    '-m',
    'pip',
    'wheel',
    '--no-deps',
    '-r',
    requirements,
    '--wheel-dir',
    wheelhouse
  ]
  if (windows)
    wheelArgs.push(
      '--extra-index-url',
      `https://download.pytorch.org/whl/${values.accelerator === 'cuda' ? values.cuda : 'cpu'}`
    )
  await run(builderPython, wheelArgs)
  const binaries = path.join(work, 'binaries-' + platform)
  await reset(binaries)
  await run(builderPython, [
    '-m',
    'pip',
    'download',
    '--only-binary=:all:',
    '--no-deps',
    '--dest',
    binaries,
    'uv==0.13.6',
    'imageio-ffmpeg==0.6.0'
  ])
  // uv 的 wheel 带有完整独立可执行程序，安装到隔离工具环境后复制。
  await run(builderPython, [
    '-m',
    'pip',
    'install',
    '--no-index',
    '--find-links',
    binaries,
    '--no-deps',
    'uv==0.13.6'
  ])
  await mkdir(path.join(stage, 'uv'), { recursive: true })
  await cp(
    path.join(tools, windows ? 'Scripts/uv.exe' : 'bin/uv'),
    path.join(stage, 'uv', windows ? 'uv.exe' : 'uv')
  )
  const ffmpegWheel = (await readdir(binaries)).find((name) => name.startsWith('imageio_ffmpeg-'))
  await run(python.executable, [script, 'ffmpeg', stage, path.join(binaries, ffmpegWheel)])
  await run(python.executable, [script, 'wheel-lock', stage])
  await mkdir(path.join(stage, 'licenses/uv'), { recursive: true })
  await cp(
    path.join(
      tools,
      windows
        ? 'Lib/site-packages/uv-0.13.6.dist-info/licenses'
        : 'lib/python3.11/site-packages/uv-0.13.6.dist-info/licenses'
    ),
    path.join(stage, 'licenses/uv'),
    { recursive: true }
  )
  await cp(path.join(project, 'python/pyproject.toml'), path.join(stage, 'source-pyproject.toml'))
  await cp(path.join(project, 'python/uv.lock'), path.join(stage, 'source-uv.lock'))
}

/** 用实际客户端的解包和安装器验证归档，不以构建机环境代替用户安装。 */
async function verify(archive, metadata, target, python) {
  const hash = createHash('sha256')
  for await (const chunk of createReadStream(archive)) hash.update(chunk)
  if ((await stat(archive)).size !== metadata.sizeBytes || hash.digest('hex') !== metadata.sha256)
    throw new Error('归档大小或 SHA-256 与构建声明不一致')
  const destination = path.join(work, '验收 路径', target)
  await reset(destination)
  await requireSpace(metadata.expandedBytes * 2 + 512 * 2 ** 20)
  await extractArchive(archive, destination, 'zip', metadata.expandedBytes)
  const info = JSON.parse(await readFile(path.join(destination, 'build-info.json'), 'utf8'))
  if (info.kind !== values.kind || info.target !== target || info.version !== values.version)
    throw new Error('归档内的目标和版本与本次验收参数不一致')
  const electron = createRequire(import.meta.url)('electron')
  const component = { id: values.kind + '.runtime', target }
  const runtime = await prepareRuntime(
    component,
    destination,
    undefined,
    async (executable, args, options) => {
      if (values.kind === 'asr') executable = electron
      if (values['skip-gpu-test'] && values.accelerator === 'cuda' && args.includes('-c'))
        args = args.map((arg) =>
          arg.replace(
            'assert torch.cuda.is_available() if True else True',
            'assert torch.version.cuda is not None'
          )
        )
      await run(executable, args, {
        ...options,
        env: { ...options.env, UV_NO_CACHE: '1', UV_LINK_MODE: 'copy' }
      })
    }
  )
  if (values.kind === 'gsv') {
    await run(
      path.join(destination, 'uv', windows ? 'uv.exe' : 'uv'),
      ['pip', 'check', '--python', runtime.pythonExecutable],
      { env: { UV_OFFLINE: '1' } }
    )
    if (values.accelerator === 'cuda' && !values['skip-gpu-test'])
      await run(runtime.pythonExecutable, [
        '-I',
        '-c',
        "import torch; assert torch.ones(8,device='cuda').sum().item() == 8; torch.cuda.synchronize()"
      ])
  }
  if (!windows) await run(python.executable, [script, 'audit-macos', destination])
  if (values.kind === 'asr' && values['asr-model']) {
    if (!values['asr-audio']) throw new Error('ASR 推理验收需要同时指定 --asr-model 和 --asr-audio')
    await run(
      electron,
      [
        path.join(project, 'scripts/runtime/smoke-asr.mjs'),
        runtime.entry,
        path.resolve(values['asr-model']),
        path.resolve(values['asr-audio']),
        values['asr-language'] ?? ''
      ],
      { env: { ELECTRON_RUN_AS_NODE: '1' } }
    )
  }
  if (values.kind === 'gsv') {
    for (const config of values['gsv-config'] ?? []) {
      const testEnv = {
        DSAKIKO_TEST_SPEECH_CONFIG: path.resolve(config),
        HF_HUB_OFFLINE: '1',
        PATH: path.dirname(runtime.ffmpegExecutable) + path.delimiter + env.PATH
      }
      if (values['nltk-data']) testEnv.NLTK_DATA = path.resolve(values['nltk-data'])
      if (values['openjtalk-dict'])
        testEnv.OPEN_JTALK_DICT_DIR = path.resolve(values['openjtalk-dict'])
      await run(
        runtime.pythonExecutable,
        [
          '-m',
          'unittest',
          'discover',
          '-s',
          'python/tests',
          '-p',
          'test_gpt_sovits_inference.py',
          '-v'
        ],
        { env: testEnv }
      )
    }
  }
  async function size(directory) {
    let bytes = 0
    for (const item of await readdir(directory, { withFileTypes: true })) {
      const file = path.join(directory, item.name)
      if (item.isDirectory()) bytes += await size(file)
      else bytes += (await stat(file)).size
    }
    return bytes
  }
  const installBytes =
    values.kind === 'gsv' ? Math.ceil((await size(path.join(destination, 'env'))) * 1.15) : 0
  if (!values['keep-work']) await reset(destination)
  return { installBytes, runtime, destination }
}

/** 发布清单只在归档验证成功后更新，同目录其他目标平台的条目保留。 */
async function main() {
  if (values.help) {
    console.log(
      'node scripts/runtime/build.mjs --kind asr|gsv [--accelerator cpu|cuda] [--cuda cu126|cu128|cu118] [--version 1.0.0] [--skip-gpu-test] [--keep-work]'
    )
    console.log(
      '验收：--verify-existing 或 --verify-candidate；ASR：--asr-model DIR --asr-audio WAV；GSV：--gsv-config JSON（可重复） --nltk-data DIR --openjtalk-dict DIR'
    )
    return
  }
  if (!['asr', 'gsv'].includes(values.kind) || !['darwin-arm64', 'win32-x64'].includes(platform))
    throw new Error('仅支持原生 macOS arm64 / Windows x64，以及 asr / gsv 两类环境')
  if (
    !['cpu', 'cuda'].includes(values.accelerator) ||
    (values.accelerator === 'cuda' && (!windows || values.kind !== 'gsv'))
  )
    throw new Error('只有 Windows GSV 可以选择 CUDA')
  if (!['cu118', 'cu126', 'cu128'].includes(values.cuda)) throw new Error('不支持的 CUDA 构建')
  if (
    values['skip-gpu-test'] &&
    (!windows || values.kind !== 'gsv' || values.accelerator !== 'cuda')
  )
    throw new Error('--skip-gpu-test 仅用于 Windows CUDA 候选包')
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(values.version)) throw new Error('版本格式无效')
  if (values['verify-existing'] && values['verify-candidate'])
    throw new Error('不能同时验收发布归档和工作目录候选归档')
  await mkdir(work, { recursive: true })
  await mkdir(output, { recursive: true })
  const target = values.kind === 'asr' ? platform : platform + '-' + values.accelerator
  const lock = path.join(work, 'build.lock')
  await writeFile(lock, String(process.pid), { flag: 'wx' })
  try {
    await requireSpace(values.kind === 'gsv' ? 4 * 2 ** 30 : 2 * 2 ** 30)
    const python = await pythonTool()
    const stage = path.join(work, 'stage-' + values.kind + '-' + target)
    if (!values['verify-existing'] && !values['verify-candidate']) {
      await reset(stage)
      if (values.kind === 'asr') await prepareAsr(stage)
      else await prepareGsv(stage, python)
      const provenance = {
        kind: values.kind,
        target,
        version: values.version,
        python: '3.11.18',
        uv: '0.13.6',
        builtAt: new Date().toISOString(),
        cuda: values.accelerator === 'cuda' ? values.cuda : null
      }
      await writeFile(
        path.join(stage, 'build-info.json'),
        JSON.stringify(provenance, null, 2) + '\n'
      )
      if (!windows) await run(python.executable, [script, 'audit-macos', stage])
    }
    const candidate = path.join(work, `${values.kind}-${target}.zip`)
    if (values['verify-existing']) {
      const previous = JSON.parse(
        await readFile(path.join(output, `${values.kind}-${target}.build.json`), 'utf8')
      )
      if (previous.target !== target || previous.kind !== values.kind)
        throw new Error('归档平台与当前验收目标不符')
      if (values.accelerator === 'cuda' && previous.cuda !== values.cuda)
        throw new Error('请用 --cuda 指定原归档记录中的 CUDA 变体')
      values.version = previous.version
      await cp(path.join(output, `${values.kind}-${target}.zip`), candidate)
      await writeFile(
        candidate.replace(/\.zip$/, '.metadata.json'),
        JSON.stringify(previous.package.files[0])
      )
    } else if (!values['verify-candidate']) {
      await run(python.executable, [script, 'zip', stage, candidate])
    }
    const metadata = JSON.parse(
      await readFile(candidate.replace(/\.zip$/, '.metadata.json'), 'utf8')
    )
    // 释放未安装材料，给真实离线安装留出空间。
    if (!values['keep-work']) await reset(stage)
    const validation = await verify(candidate, metadata, target, python)
    const provenance = {
      kind: values.kind,
      target,
      version: values.version,
      python: '3.11.18',
      uv: '0.13.6',
      validatedAt: new Date().toISOString(),
      cuda: values.accelerator === 'cuda' ? values.cuda : null
    }
    const item = {
      kind: values.kind,
      target,
      version: values.version,
      setupApiVersion: 1,
      installBytes: validation.installBytes,
      files: [metadata]
    }
    const manifestPath = path.join(output, 'release.json')
    let manifest = { schemaVersion: 1, packages: [] }
    try {
      manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
    } catch (error) {
      if (error.code !== 'ENOENT') throw error
    }
    manifest.packages = manifest.packages.filter(
      (entry) => !(entry.kind === item.kind && entry.target === target)
    )
    // 缺 GPU 的机器只能产出候选包，不能把未验收 CUDA 包混进正式清单。
    if (!values['skip-gpu-test']) manifest.packages.push(item)
    runtimePackages(manifest)
    await cp(candidate, path.join(output, metadata.path))
    await writeFile(
      path.join(output, metadata.path.replace('.zip', '.build.json')),
      JSON.stringify(
        {
          ...provenance,
          validation: {
            archive: true,
            offlineInstall: true,
            nativeImports: true,
            realInference:
              values.kind === 'asr' ? !!values['asr-model'] : !!values['gsv-config']?.length,
            inferenceDevice: 'cpu',
            gpu: values.accelerator === 'cuda' && !values['skip-gpu-test']
          },
          package: item
        },
        null,
        2
      ) + '\n'
    )
    const temporary = manifestPath + '.tmp'
    await writeFile(temporary, JSON.stringify(manifest, null, 2) + '\n')
    const { rename } = await import('node:fs/promises')
    await rename(temporary, manifestPath)
    await rm(candidate, { force: true })
    console.log('已生成：', path.join(output, metadata.path))
  } finally {
    await rm(lock, { force: true })
  }
}

await main()

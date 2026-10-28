import os from 'node:os'
import si from 'systeminformation'
import { parameters, syncTool } from './definition.js'

/** 可选传感器查询独立失败，避免缺少 GPU 驱动使整份系统状态不可用。 */
async function metric(query, signal) {
  let timer
  let abort
  try {
    return await Promise.race([
      Promise.resolve().then(query),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('timeout')), 4000)
      }),
      new Promise((_, reject) => {
        abort = () => reject(signal.reason)
        if (signal?.aborted) abort()
        else signal?.addEventListener('abort', abort, { once: true })
      })
    ])
  } catch {
    signal?.throwIfAborted()
    return null
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
  }
}

/**
 * 创建跨平台硬件查询工具，仅返回相关指标，不向模型转发设备标识。
 * @param {object} hardware systeminformation 或兼容查询实现。
 * @returns {object} 硬件工具条目。
 */
export function systemTool(hardware = si) {
  return syncTool(
    {
      name: 'get_system_hardware_status',
      description:
        '获取当前运行设备（对方电脑）的操作系统、CPU、内存及可用 GPU 状态指标；无法采集的单项指标返回 null。',
      requiredCapabilities: ['system'],
      parametersSchema: parameters({
        include_gpu: {
          type: 'boolean',
          description: '是否采集 GPU 状态指标（默认 true；仅需 CPU/内存信息时可传 false 以加快响应）。'
        }
      })
    },
    async (_, input, source, signal) => {
      const [cpu, load, memory, graphics] = await Promise.all([
        metric(() => hardware.cpu(), signal),
        metric(() => hardware.currentLoad(), signal),
        metric(() => hardware.mem(), signal),
        input.include_gpu === false ? null : metric(() => hardware.graphics(), signal)
      ])
      const value = (number) => (Number.isFinite(number) && number >= 0 ? number : null)
      return {
        platform: os.platform(),
        release: os.release(),
        architecture: os.arch(),
        sampledAt: new Date().toISOString(),
        cpu: {
          model: cpu?.brand ?? os.cpus()[0]?.model ?? null,
          physicalCores: value(cpu?.physicalCores),
          logicalCores: cpu?.cores ?? os.cpus().length,
          usagePercent: value(load?.currentLoad)
        },
        memory: memory
          ? {
              totalBytes: memory.total,
              availableBytes: memory.available,
              activeBytes: memory.active,
              usedIncludingCacheBytes: memory.used
            }
          : { totalBytes: os.totalmem(), freeBytes: os.freemem() },
        gpu: {
          reason:
            input.include_gpu === false ? 'disabled' : graphics ? null : 'unavailable_or_timeout',
          controllers: (graphics?.controllers ?? []).map((gpu) => ({
            model: gpu.model,
            vendor: gpu.vendor,
            sharedMemory: gpu.vramDynamic ?? null,
            vramMiB: value(gpu.vram),
            usagePercent: value(gpu.utilizationGpu),
            memoryUsedMiB: value(gpu.memoryUsed),
            temperatureCelsius: value(gpu.temperatureGpu),
            metricsUnavailable: !Number.isFinite(gpu.utilizationGpu)
          }))
        }
      }
    }
  )
}

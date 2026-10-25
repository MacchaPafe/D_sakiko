/**
 * 检查是否仍可能有进程使用资源；POSIX 进程组包含推理 worker，不能只检查 HTTP 父进程。
 * Windows 按语音主进程存活判断；正常关闭由 Python 负责先确认 worker 结束。
 * @param {{processId?: number, processGroupId?: number}} lease 进程保护记录。
 * @returns {boolean} 仍需保留资源。
 */
export function processUsesResources(lease) {
  if (!lease.processId) return false
  try {
    process.kill(lease.processGroupId ? -lease.processGroupId : lease.processId, 0)
    return true
  } catch (error) {
    return error.code !== 'ESRCH'
  }
}

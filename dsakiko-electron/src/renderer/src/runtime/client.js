import { createElectronClient } from './electron'

/** @type {import('../../../shared/contracts/runtime.js').RuntimeClient} */
export const runtimeClient = createElectronClient()

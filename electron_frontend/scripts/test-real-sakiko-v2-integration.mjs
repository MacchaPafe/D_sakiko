import { createHash } from 'node:crypto'
import fs from 'node:fs'
import assert from 'node:assert/strict'

const manifestPath = process.env.DSAKIKO_REAL_SAKIKO_V2_MANIFEST
if (!manifestPath) {
  console.log('SKIP: set DSAKIKO_REAL_SAKIKO_V2_MANIFEST for the optional real-model integration check.')
  process.exit(0)
}
assert.ok(fs.existsSync(manifestPath), `real manifest not found: ${manifestPath}`)
const source = fs.readFileSync(manifestPath)
assert.equal(
  createHash('sha256').update(source).digest('hex').toUpperCase(),
  'C546A5A94748FF71723340CFCDF5871F25C5099A0060F534C739A2E0ABB263E8',
  'the optional integration source must be the recorded Sakiko costume V2 manifest',
)
console.log('Optional Sakiko V2 integration manifest check passed.')

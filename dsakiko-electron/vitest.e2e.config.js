import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/e2e/**/*.test.js'],
    fileParallelism: false,
    testTimeout: 30000,
    hookTimeout: 30000
  }
})

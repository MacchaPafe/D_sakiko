import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.{js,jsx}', 'tests/integration/**/*.test.js'],
    passWithNoTests: true
  }
})

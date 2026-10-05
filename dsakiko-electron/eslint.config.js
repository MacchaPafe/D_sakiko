import { builtinModules } from 'node:module'
import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import prettier from 'eslint-config-prettier'

export default [
  {
    ignores: [
      'node_modules/**',
      'out/**',
      'dist/**',
      'test-results/**',
      'coverage/**',
      'src/renderer/public/**',
      'python/vendor/**'
    ]
  },
  js.configs.recommended,
  {
    files: [
      '*.js',
      'src/main/**/*.js',
      'src/preload/**/*.js',
      'src/backend/**/*.js',
      'scripts/**/*.{js,mjs}'
    ],
    languageOptions: { globals: globals.node }
  },
  {
    files: ['tests/**/*.js'],
    languageOptions: { globals: { ...globals.node, ...globals.browser } }
  },
  {
    files: ['src/renderer/src/**/*.{js,jsx}'],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } }
    },
    plugins: { react, 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    settings: { react: { version: 'detect' } },
    rules: {
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      'react/prop-types': 'off',
      'react-refresh/only-export-components': ['error', { allowConstantExport: true }],
      'no-restricted-imports': [
        'error',
        {
          paths: ['electron', ...builtinModules],
          patterns: [
            {
              group: ['node:*'],
              message: '前端通过 runtime 调用宿主能力。'
            },
            {
              group: ['**/backend/**', '**/main/**', '**/preload/**'],
              message: '前端只共享 contracts 数据约定。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['src/backend/**/*.js', 'src/shared/**/*.js'],
    rules: { 'no-restricted-imports': ['error', { paths: ['electron'] }] }
  },
  {
    files: ['src/shared/**/*.js'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          paths: ['electron', 'react', 'react-dom', ...builtinModules],
          patterns: [
            {
              group: ['node:*'],
              message: '共享协议必须能够在各运行环境中使用。'
            }
          ]
        }
      ]
    }
  },
  prettier
]

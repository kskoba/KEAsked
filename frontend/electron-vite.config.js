import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()]
    // entry: src/main/index.js  (auto-discovered)
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    // entry: src/preload/index.js  (auto-discovered)
    build: {
      rollupOptions: {
        output: {
          format: 'cjs',
          entryFileNames: '[name].js'
        }
      }
    }
  },
  renderer: {
    plugins: [react()]
    // entry: src/renderer/index.html  (auto-discovered)
  }
})

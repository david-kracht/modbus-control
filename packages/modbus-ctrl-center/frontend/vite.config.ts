import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'url'
import { dirname, resolve } from 'path'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

export default defineConfig(({ mode }) => {
  // Load environment variables from the monorepo root
  const env = loadEnv(mode, resolve(__dirname, '../../..'), '')
  
  const backendHost = env.CTRL_CENTER_HOST || '127.0.0.1'
  const backendPort = env.CTRL_CENTER_PORT || '8000'
  const backendUrl = `http://${backendHost}:${backendPort}`
  const wsUrl = `ws://${backendHost}:${backendPort}`

  return {
    plugins: [react()],
    build: {
      outDir: resolve(__dirname, '../src/modbus_ctrl_center/static'),
      emptyOutDir: true,
    },
    server: {
      proxy: {
        '/api': {
          target: backendUrl,
          changeOrigin: true,
        },
        '/ws': {
          target: wsUrl,
          ws: true,
        }
      }
    }
  }
})

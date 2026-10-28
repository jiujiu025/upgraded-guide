import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import fs from 'node:fs'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue()],
    server: {
      port: 5173,
      https: env.VITE_HTTPS === 'true' && env.VITE_HTTPS_CERT && env.VITE_HTTPS_KEY
        ? { cert: fs.readFileSync(env.VITE_HTTPS_CERT), key: fs.readFileSync(env.VITE_HTTPS_KEY) }
        : undefined,
      proxy: { '/api': env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000' },
    },
  }
})

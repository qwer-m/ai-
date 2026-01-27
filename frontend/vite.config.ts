import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 强制 HMR (热更新) 连接到 5173，防止在后端代理模式(8000)下连接失败
    hmr: {
      clientPort: 5173,
    },
    port: 5173, // Force port 5173
    strictPort: true, // Fail if port is busy instead of auto-incrementing
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})

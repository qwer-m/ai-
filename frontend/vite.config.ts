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
    port: 5173, // 强制使用端口 5173
    strictPort: true, // 如果端口被占用则失败，而不是自动递增
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
        // Rewrite path to remove trailing slashes if present
        rewrite: (path) => path.replace(/\/$/, '')
      }
    }
  }
})

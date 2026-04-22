import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // 生产环境基础路径,部署到子应用时使用相对路径
  base: './',
  server: {
    host: '0.0.0.0',
    port: 3000,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path,
        // SSE需要禁用缓冲,否则会一次性返回所有数据
        configure: (proxy, options) => {
          proxy.on('proxyRes', (proxyRes, req, res) => {
            // 对SSE流式响应禁用缓冲
            if (proxyRes.headers['content-type'] === 'text/event-stream') {
              res.setHeader('X-Accel-Buffering', 'no');
              res.setHeader('Cache-Control', 'no-cache');
              res.setHeader('Connection', 'keep-alive');
            }
          });
        }
      }
    }
  }
})

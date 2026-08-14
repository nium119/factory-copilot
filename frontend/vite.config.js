import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    base: env.VITE_BASE || './',
    server: {
      host: '0.0.0.0',
      port: 5004,
      strictPort: true,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:9004',
          changeOrigin: true,
          ws: true,
          configure: (proxy, options) => {
            proxy.on('proxyRes', (proxyRes, req, res) => {
              if (proxyRes.headers['content-type'] === 'text/event-stream') {
                res.setHeader('X-Accel-Buffering', 'no');
                res.setHeader('Cache-Control', 'no-cache');
                res.setHeader('Connection', 'keep-alive');
              }
            });
          },
        },
        '/SysWebApi': {
          target: 'http://172.21.10.18:99',
          changeOrigin: true,
        },
      }
    }
  }
})

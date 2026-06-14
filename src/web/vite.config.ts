import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'

const apiProxy = {
  target: 'http://127.0.0.1:5086',
  changeOrigin: true,
  ws: true,
}

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 8086,
    strictPort: true,
    proxy: {
      '/api': apiProxy,
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 8086,
    strictPort: true,
    proxy: {
      '/api': apiProxy,
    },
  },
})

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Python server serves the build from web/dist and owns /api.
export default defineConfig({
    plugins: [react()],
    build: { outDir: 'dist', emptyOutDir: true },
    server: {
        port: 5180,
        proxy: {
            '/api': { target: process.env.WAREHOUSEIQ_API ?? 'http://127.0.0.1:8090', changeOrigin: true },
        },
    },
})

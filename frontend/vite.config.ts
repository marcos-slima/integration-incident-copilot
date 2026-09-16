import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // /diagnose, /health, /.well-known → FastAPI em localhost:8000
      '/diagnose': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/.well-known': 'http://localhost:8000',
    },
  },
  build: {
    // Gera os arquivos em ../static/dist para o FastAPI servir
    outDir: '../static/dist',
    emptyOutDir: true,
  },
});

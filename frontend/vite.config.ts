import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // /diagnose (inclui /diagnose/async), /auth, /health, /.well-known
      // → FastAPI em localhost:8000. Sem '/auth' o login (DA-54) caia no
      // proprio Vite no modo dev (FE-01, validacao 2026-10-07).
      '/diagnose': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
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

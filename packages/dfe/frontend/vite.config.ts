import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Shell do DF-e Copilot (DA-95). No dev, a API do produto
// (uvicorn dfe_copilot.main:app) responde em :8000.
const api = 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: { '/diagnose': api, '/domain-plugins': api, '/auth': api, '/health': api },
  },
  build: {
    // dfe_copilot.main serve este diretorio (UI_DIST_DIR)
    outDir: 'dist',
    emptyOutDir: true,
  },
});

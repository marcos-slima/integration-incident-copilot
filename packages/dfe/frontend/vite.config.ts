import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Shell do DF-e Copilot (DA-95). No dev, a API do produto
// usa :8001 para coexistir com o IIC em :8000. Pode ser sobrescrita
// no ambiente do processo Vite (ex.: DFE_API_URL=http://127.0.0.1:8010).
const api = process.env.DFE_API_URL || 'http://127.0.0.1:8001';

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

import { defineConfig } from 'vite';
import { readFileSync } from 'node:fs';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));
const peers = Object.keys(pkg.peerDependencies);

export default defineConfig({
  build: {
    lib: { entry: 'src/index.ts', formats: ['es'], fileName: () => 'index.js' },
    rollupOptions: {
      external: (id) => peers.some((peer) => id === peer || id.startsWith(`${peer}/`)),
    },
  },
  plugins: [{
    name: 'public-styles',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'styles.css', source: readFileSync(new URL('./src/styles.css', import.meta.url), 'utf8') });
    },
  }],
});

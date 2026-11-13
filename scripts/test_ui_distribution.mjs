// DA-95: builds dos shells contra o tarball, fora dos fontes do workspace.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readdirSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const work = mkdtempSync(join(tmpdir(), 'copilot-ui-distribution-'));
const run = (cmd, args, cwd = root) => execFileSync(cmd, args, { cwd, stdio: 'inherit' });
try {
  const metadata = JSON.parse(execFileSync('npm', [
    'pack', '-w', '@copilot/core-ui', '--ignore-scripts', '--json', '--pack-destination', work,
  ], { cwd: root, encoding: 'utf8' }));
  const packed = Object.values(metadata)[0];
  assert(packed.files.some((f) => f.path === 'dist/index.js'));
  assert(packed.files.some((f) => f.path === 'dist/index.d.ts'));
  assert(packed.files.some((f) => f.path === 'dist/styles.css'));
  assert(!packed.files.some((f) => f.path.startsWith('src/')));
  const modules = join(work, 'node_modules');
  mkdirSync(join(modules, '@copilot/core-ui'), { recursive: true });
  run('tar', ['-xzf', join(work, packed.filename), '--strip-components=1', '-C', join(modules, '@copilot/core-ui')]);
  // Reutiliza apenas dependências de terceiros já instaladas. O namespace
  // @copilot contém exclusivamente o pacote distribuído acima.
  for (const entry of readdirSync(join(root, 'node_modules'))) {
    if (entry === '@copilot' || entry === '.bin') continue;
    symlinkSync(join(root, 'node_modules', entry), join(modules, entry));
  }
  for (const [name, relative] of [['iic', 'frontend'], ['dfe', 'packages/dfe/frontend']]) {
    const consumer = join(work, name);
    mkdirSync(consumer);
    for (const entry of ['src', 'public', 'index.html', 'package.json', 'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json', 'vite.config.ts']) {
      cpSync(join(root, relative, entry), join(consumer, entry), { recursive: true });
    }
    run(process.execPath, [join(root, 'node_modules/typescript/bin/tsc'), '-b'], consumer);
    run(process.execPath, [join(root, 'node_modules/vite/bin/vite.js'), 'build'], consumer);
    console.log(`${name}: build passou com core-ui do tarball, fora do workspace`);
  }
} finally {
  rmSync(work, { recursive: true, force: true });
}

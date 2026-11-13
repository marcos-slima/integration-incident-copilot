import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiError, getDomainPlugins, HTTP_CONTRACT_VERSION } from '../dist/index.js';

test('catalogue rejects incompatible API and preserves legacy v1', async () => {
  const original = globalThis.fetch;
  try {
    for (const version of ['2.0', '1.1', 'invalid']) {
      globalThis.fetch = async (_url, init) => {
        assert.equal(init.headers['X-Core-Contract-Version'], HTTP_CONTRACT_VERSION);
        return new Response(JSON.stringify({ contract_version: version, plugins: [] }));
      };
      await assert.rejects(getDomainPlugins(), (error) => error instanceof ApiError && error.code === 'incompatible_contract');
    }
    for (const version of ['1.0', undefined]) {
      globalThis.fetch = async () => new Response(JSON.stringify({ contract_version: version, plugins: [] }));
      assert.deepEqual(await getDomainPlugins(), []);
    }
  } finally { globalThis.fetch = original; }
});

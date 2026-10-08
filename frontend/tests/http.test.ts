import test from 'node:test';
import assert from 'node:assert/strict';
import { fetchJson } from '../src/services/http';

test('行情请求在等待响应头和读取响应正文时都能超时，随后可重试', async () => {
  const original = globalThis.fetch;
  try {
    for (const stage of ['headers', 'body']) {
      globalThis.fetch = (async (_url: any, init: any) => {
        const blocked = () => new Promise<never>((_, reject) => {
          init.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
        });
        if (stage === 'headers') return blocked();
        return { status: 200, json: blocked };
      }) as typeof fetch;
      await assert.rejects(fetchJson('/api/tools/study', {}, { timeoutMs: 15 }), /请求超时/);
    }
    globalThis.fetch = (async () => ({ status: 200, json: async () => ({ bars: [1] }) })) as typeof fetch;
    assert.deepEqual(await fetchJson('/api/tools/study', {}), { status: 200, data: { bars: [1] } });
  } finally { globalThis.fetch = original; }
});

test('取消载入会终止请求，并区分取消与服务端失败', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = (async (_url: any, init: any) => new Promise<never>((_, reject) => {
      init.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
    })) as typeof fetch;
    const cancel = new AbortController();
    const pending = fetchJson('/api/tools/study', {}, { signal: cancel.signal });
    cancel.abort();
    await assert.rejects(pending, /已取消/);
    globalThis.fetch = (async () => ({ status: 400, json: async () => ({ detail: '行情序列为空' }) })) as typeof fetch;
    assert.deepEqual(await fetchJson('/api/tools/study', {}), { status: 400, data: { detail: '行情序列为空' } });
  } finally { globalThis.fetch = original; }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { createPollLoader } from './poll-loader.js';

test('invalid JSON cannot poison cache and identical responses do not publish', async () => {
  let text = '{"games":[1]}';
  const updates = [];
  const loader = createPollLoader({ picks: '/picks' }, v => updates.push(v), {
    fetcher: async () => ({ ok: true, text: async () => text }),
  });
  await loader.load(); await loader.load();
  text = '<html>502</html>';
  await loader.load(); await loader.load();
  assert.equal(updates.length, 1);
  assert.deepEqual(updates[0].data.picks, { games: [1] });
  text = '{"games":[2]}';
  await loader.load();
  assert.equal(updates.length, 2);
  assert.deepEqual(updates[1].data.picks, { games: [2] });
  loader.stop();
});

test('overlapping events share one batch and cleanup aborts it', async () => {
  let calls = 0, signal;
  const updates = [];
  const loader = createPollLoader({ picks: '/picks' }, v => updates.push(v), {
    fetcher: async (_, options) => {
      calls++; signal = options.signal;
      return new Promise((_, reject) => signal.addEventListener('abort', () => reject(new Error('abort'))));
    },
  });
  const pending = loader.load();
  await loader.load();
  assert.equal(calls, 1);
  loader.stop(); await pending;
  assert.equal(signal.aborted, true);
  assert.equal(updates.length, 0);
});

test('timeout releases batch; partial failures retain healthy sources without reparsing', async () => {
  let hang = true;
  const updates = [];
  const loader = createPollLoader({ picks: '/picks', scores: '/scores' }, v => updates.push(v), {
    timeoutMs: 10,
    fetcher: async (url, { signal }) => {
      if (hang && url.startsWith('/picks')) return new Promise((_, reject) =>
        signal.addEventListener('abort', () => reject(new Error('timeout'))));
      return { ok: true, text: async () => '{"ok":true}' };
    },
  });
  await loader.load();
  assert.equal(updates[0].data.picks, null);
  assert.deepEqual(updates[0].data.scores, { ok: true });
  hang = false; await loader.load();
  assert.deepEqual(updates[1].data.picks, { ok: true });
  assert.equal(updates[0].data.scores, updates[1].data.scores);
  loader.stop();
});

test('a missing summary route falls back once and keeps conditional polls on the correct URL', async () => {
  const calls = [], updates = [];
  const loader = createPollLoader({scores: '/summary'}, value => updates.push(value), {
    fallbacks: {scores: '/legacy'},
    fetcher: async (url, options) => {
      calls.push([url.split('?')[0], options.headers['If-None-Match']]);
      if (url.startsWith('/summary')) return {status: 404};
      if (options.headers['If-None-Match']) return {status: 304};
      return {ok: true, status: 200, headers: {get: () => 'tag'}, text: async () => '{"games":[]}'};
    },
  });
  await loader.load();
  await loader.load();
  assert.deepEqual(calls, [['/summary', undefined], ['/legacy', undefined], ['/legacy', 'tag']]);
  assert.equal(updates.length, 1);
  loader.stop();
});

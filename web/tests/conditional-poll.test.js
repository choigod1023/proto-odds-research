import test from 'node:test';
import assert from 'node:assert/strict';
import { createPollLoader } from '../src/lib/poll-loader.js';

const response = (status, body, etag = null, onText = () => {}) => ({
  status, ok: status >= 200 && status < 300,
  headers: { get: () => etag },
  async text() { onText(); return body; },
});

test('unchanged conditional response retains data without consuming or parsing body', async () => {
  const published = [], calls = []; let reads = 0;
  const original = JSON.stringify({generated_at:'old', live:[{name:'경기', options:[1,2]}]});
  const replies = [response(200, original, 'W/"a"', () => reads++),
    response(304, '', 'W/"a"', () => { throw new Error('304 body read'); }),
    response(200, JSON.stringify({generated_at:'new',live:[]}), 'W/"b"', () => reads++)];
  const loader = createPollLoader({picks:'https://test/api/picks'}, value => published.push(value), {
    fetcher: async (url, options) => { calls.push({url, options}); return replies.shift(); },
  });
  await loader.load(); await loader.load(); await loader.load(); loader.stop();
  assert.equal(published.length, 2); assert.equal(reads, 2);
  assert.deepEqual(published[0].data.picks, JSON.parse(original));
  assert.equal(published[1].data.picks.generated_at, 'new');
  assert.equal(calls[0].options.headers['If-None-Match'], undefined);
  assert.equal(calls[1].options.headers['If-None-Match'], 'W/"a"');
  assert.equal(calls[2].options.headers['If-None-Match'], 'W/"a"');
  assert.equal(calls[1].options.cache, 'no-store');
});

test('older servers and failures keep last good data and allow later refresh', async () => {
  const published = []; const calls = [];
  const replies = [response(200, '{"x":1}'), response(200, '{"x":1}'), response(503, ''), response(200, '{"x":2}')];
  const loader = createPollLoader({picks:'/api/picks'}, value => published.push(value), {
    fetcher: async (_url, options) => { calls.push(options); return replies.shift(); },
  });
  for (let i=0;i<4;i++) await loader.load();
  loader.stop(); assert.deepEqual(published.map(p => p.data.picks.x), [1,2]);
  assert.ok(calls.every(c => !c.headers['If-None-Match']));
});

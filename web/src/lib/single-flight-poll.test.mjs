import test from 'node:test';
import assert from 'node:assert/strict';
import {singleFlightPoll} from './single-flight-poll.js';

test('focus and timer do not duplicate an outstanding request; next cycle resumes', async () => {
  let calls = 0, resolve;
  const values = [];
  const poll = singleFlightPoll({read: () => { calls++; return new Promise(r => { resolve = r; }); },
    success: d => values.push(d), failure: assert.fail});
  const first = poll.load();
  await poll.load(); await poll.load();
  assert.equal(calls, 1);
  resolve('old'); await first;
  const next = poll.load(); resolve('new'); await next;
  assert.deepEqual(values, ['old', 'new']);
  poll.stop(); await poll.load(); assert.equal(calls, 2);
});

test('abort timeout preserves last success via failure callback and releases next request', async () => {
  let last = 'previous', failures = 0, calls = 0;
  const poll = singleFlightPoll({timeoutMs: 5,
    read: signal => { calls++; return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('timeout')))); },
    success: d => { last = d; }, failure: () => { failures++; }});
  await poll.load(); await poll.load();
  assert.equal(last, 'previous'); assert.equal(failures, 2); assert.equal(calls, 2);
  poll.stop();
});

test('unmount aborts without a late callback, even when the read resolves later', async () => {
  let signal, resolve, callbacks = 0;
  const poll = singleFlightPoll({read: s => {signal=s; return new Promise(r=>{resolve=r;});},
    success: () => callbacks++, failure: () => callbacks++});
  const pending = poll.load(); poll.stop();
  assert.equal(signal.aborted, true);
  resolve({}); await pending; assert.equal(callbacks, 0);
});

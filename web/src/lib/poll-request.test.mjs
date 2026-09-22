import test from 'node:test';
import assert from 'node:assert/strict';
import { createJsonPoll } from './poll-request.js';

test('focus/online/timer overlap sends one request and allows subsequent refresh', async t => {
  let release, calls = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    calls++;
    return {ok:true, json:() => new Promise(resolve => {release=resolve;})};
  });
  const values=[];
  const poll=createJsonPoll('/api/picks', d=>values.push(d), ()=>assert.fail());
  const first=poll.load();
  await poll.load();
  assert.equal(calls,1);
  release({live:[]}); await first;
  assert.equal(values.length,1);
  const second=poll.load(); await Promise.resolve();
  release({live:[1]}); await second;
  assert.equal(calls,2);
  poll.stop();
});

test('timeout covers stalled body, reports failure and permits retry', async t => {
  let failures=0;
  t.mock.method(globalThis,'fetch',async (_, {signal}) => ({ok:true,json:()=>new Promise((_,reject)=>{
    signal.addEventListener('abort',()=>reject(new Error('aborted')));
  })}));
  const poll=createJsonPoll('/api/picks',()=>assert.fail(),()=>failures++,5);
  await poll.load(); await poll.load();
  assert.equal(failures,2);
  poll.stop();
});

test('unmount aborts request without publishing success or failure', async t => {
  let signal;
  t.mock.method(globalThis,'fetch',(_,options)=>new Promise((_,reject)=>{
    signal=options.signal;
    signal.addEventListener('abort',()=>reject(new Error('aborted')));
  }));
  const poll=createJsonPoll('/api/picks',()=>assert.fail(),()=>assert.fail());
  const pending=poll.load(); poll.stop(); await pending;
  assert.equal(signal.aborted,true);
  await poll.load();
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { API_ORIGIN, apiUrl } from '../src/lib/api-origin.js';
import { API } from '../src/lib/match-data.js';
import { ANONYMOUS_BETS_URL } from '../src/lib/anonymous-bets.js';

test('all API consumers retain collector default until explicit rollout', () => {
  assert.equal(API_ORIGIN, 'https://proto-odds-collector.fly.dev');
  assert.equal(API, API_ORIGIN);
  assert.equal(ANONYMOUS_BETS_URL, apiUrl('/anonymous-bets'));
  assert.equal(apiUrl('/api/matches?scope=recent'), `${API}/api/matches?scope=recent`);
});

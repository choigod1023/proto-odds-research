import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
test('count headline, small scope, labeled tiles and honest empty state',async()=>{
  const server=await createServer({configFile:false,esbuild:{jsx:'automatic'},server:{middlewareMode:true,hmr:false,watch:null},appType:'custom'});
  try {
    const {default:View}=await server.ssrLoadModule('/src/components/RecommendationResults.jsx');
    const now=Date.parse('2026-09-06T15:00:00+09:00');
    const history=Object.fromEntries(Array.from({length:10},(_,i)=>[i,{id:String(i),recommended:true,
      kickoff_at:'2026-09-06T12:00:00+09:00',published_at:'2026-09-06T09:00:00+09:00',recorded_at:'2026-09-06T09:00:00+09:00',
      result:i<7?'hit':'miss',result_source:'official',home:'H',away:'A'}]));
    const html=renderToStaticMarkup(createElement(View,{today:{recommendation_history:history},now}));
    assert.match(html,/최근 추천 10건 중/);assert.match(html,/7건 적중/);
    assert.match(html,/class="recommendation-result-scope">당시 추천 정책 기준/);
    assert.match(html,/현재 55%·배당 1.50 이상 정책만의 성적이 아닙니다/);
    assert.equal((html.match(/aria-haspopup="dialog"/g)||[]).length,10);
    assert.doesNotMatch(html.match(/<h2>.*?<\/h2>/)[0],/%/);
    const empty=renderToStaticMarkup(createElement(View,{now}));
    assert.match(empty,/추천 결과를 기다리고/);assert.doesNotMatch(empty,/0건 적중/);
  } finally {await server.close();}
});

test('headline population has visible sport totals, fixtures and full KST kickoff times',async()=>{
  const server=await createServer({configFile:false,esbuild:{jsx:'automatic'},server:{middlewareMode:true,hmr:false,watch:null},appType:'custom'});
  try {
    const {default:View}=await server.ssrLoadModule('/src/components/RecommendationResults.jsx');
    const history=Object.fromEntries(Array.from({length:11},(_,i)=>[i,{id:String(i),recommended:true,
      kickoff_at:i===10?'2025-12-30T15:30:00Z':'2025-12-31T15:30:00Z',
      published_at:'2025-12-30T09:00:00Z',recorded_at:'2025-12-30T09:00:00Z',
      sport:i<5?'sc':i<9?'bs':'unrecognized',league:i<5?'EPL':'KBO',
      result:i<7?'hit':'miss',result_source:'official',home:`홈팀${i}`,away:`원정팀${i}`} ]));
    const html=renderToStaticMarkup(createElement(View,{today:{recommendation_history:history},now:Date.parse('2026-01-02T00:00:00Z')}));
    assert.match(html,/축구 · 5건 중 5건 적중/);
    assert.match(html,/야구 · 4건 중 2건 적중/);
    assert.match(html,/종목 정보 없음 · 1건 중 0건 적중/);
    assert.match(html,/>홈팀0 vs 원정팀0<\/span>/);
    assert.match(html,/>홈팀9 vs 원정팀9<\/span>/);
    assert.doesNotMatch(html,/홈팀10/);
    assert.equal((html.match(/>2026-01-01 00:30 KST<\/time>/g)||[]).length,10);
    assert.equal((html.match(/<b>실패<\/b>/g)||[]).length,3);
    assert.match(html,/전체 적중률이나 인기 경기 순위가 아닙니다/);
  } finally {await server.close();}
});

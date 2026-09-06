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
    assert.match(html,/class="recommendation-result-scope">오늘의 추천픽 기준/);
    assert.equal((html.match(/aria-haspopup="dialog"/g)||[]).length,10);
    assert.doesNotMatch(html,/%/);
    const empty=renderToStaticMarkup(createElement(View,{now}));
    assert.match(empty,/추천 결과를 기다리고/);assert.doesNotMatch(empty,/0건 적중/);
  } finally {await server.close();}
});

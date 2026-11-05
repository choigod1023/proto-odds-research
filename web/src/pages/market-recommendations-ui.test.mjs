import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';

test('game card and detail display automatic picks without replacing winner; started and stale games do not',async()=>{
 const now=Date.now;Date.now=()=>Date.parse('2099-09-06T00:00:00Z');
 const server=await createServer({configFile:false,root:fileURLToPath(new URL('../../',import.meta.url)),esbuild:{jsx:'automatic'},optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,watch:null,hmr:false},appType:'custom'});
 try {
  const {Game}=await server.ssrLoadModule('/src/pages/Markets.jsx');
  const options=['언더','오버'].map((name,i)=>({게임번호:'2',market:'언더오버',label:'U 2.5',line:2.5,n_way:2,선택:name,배당:i?1.9:1.8,selection_id:`s${i}`,offer_id:`o${i}`,period:'full_game',settlement_scope:'regulation'}));
  const item={selection_id:'s1',offer_id:'o1',market:'언더오버',label:'U 2.5',line:2.5,selection:'오버',odds:1.9};
  const g={year:2099,date:'09.06(일) 18:00',round:1,status:'경기전',sport:'bs',league:'리그',home:'홈팀',away:'원정팀',options,market_recommendations:{version:'multi-market-recommendations-v1',items:[item]}};
  const props={g,opts:options,wait:false,onSaveBet:()=>{},todayOption:{market:'승패',선택:'홈',배당:1.8,시장확률:.6}};
  for(const detailOnly of [false,true]){
    const html=renderToStaticMarkup(createElement(Game,{...props,detailOnly}));
    assert.match(html,/언더오버 U 2\.5/);assert.match(html,/오버/);assert.match(html,/승패/);
    if(detailOnly) assert.match(html,/이 추천을 선택 목록에 담기/);
  }
  for(const extra of [{stale:true},{wait:true},{g:{...g,_liveStarted:true}},{g:{...g,_liveOddsChanged:true}}]){
    const html=renderToStaticMarkup(createElement(Game,{...props,...extra}));assert.doesNotMatch(html,/자동 추천 · 언더오버/);
  }
 } finally {Date.now=now;await server.close();}
});

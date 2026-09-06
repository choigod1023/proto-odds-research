import assert from 'node:assert/strict';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
test('record detail preserves purchased pick while showing updated game information',async()=>{
 const server=await createServer({configFile:false,root:fileURLToPath(new URL('../../',import.meta.url)),esbuild:{jsx:'automatic'},optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,watch:null},appType:'custom'});
 try {
  const {default:Detail}=await server.ssrLoadModule('/src/components/RecordMatchDetails.jsx');
  const {default:Dashboard,BetCard,ComboCard}=await server.ssrLoadModule('/src/pages/Dashboard.jsx');
  const dashboard=renderToStaticMarkup(createElement(Dashboard));
  assert.doesNotMatch(dashboard,/dashboard-recommendations|오늘의 추천 판정|추천하지 않은 후보/);
  assert.match(dashboard,/dashboard-bet-list/);
  const bet={id:'saved',createdAt:'2026-09-06T01:00:00Z',game:{sport:'bs',league:'MLB',home:'홈',away:'원정',date:'09.06 10:00',year:2026},selection:{market:'승패',choice:'홈'},purchaseOdds:1.8,stake:1000,openingProbability:.57};
  const before=JSON.stringify(bet);
  const live={status:'STARTED',observed_at:new Date().toISOString(),home_score:2,away_score:1,status_text:'3회초',bases:{first:{occupied:true,runner:'주자 이름'}},period_scores:[{period:1,home:2,away:1}]};
  const render=(lv,b=bet)=>renderToStaticMarkup(createElement(Detail,{bet:b,live:lv}));
  const html=render(live);
  assert.match(html,/내가 저장한 픽/);assert.match(html,/1.80/);assert.match(html,/주자 이름/);assert.match(html,/이닝별 스코어보드/);
  const updated=render({...live,home_score:4,away_score:1,finished:true,status:'RESULT'});
  assert.match(updated,/4 : 1/);assert.match(updated,/적중/);assert.doesNotMatch(updated,/그라운드 상황/);
  assert.match(render({...live,timeline:[{text:'득점 이벤트',type:'GOAL',side:'home',time:'21′'}]}, {...bet,game:{...bet.game,sport:'sc'}}),/득점 이벤트/);
  assert.match(render(null),/당시 경기력 근거가 저장되지 않은 기록/);
  assert.equal(JSON.stringify(bet),before);
  assert.match(renderToStaticMarkup(createElement(BetCard,{bet,live})),/aria-haspopup="dialog"/);
  const combo=renderToStaticMarkup(createElement(ComboCard,{group:{bets:[bet,{...bet,id:'second'}],ticket:{combinedOdds:3,stake:1000,expectedPayout:3000}},index:new Map()}));
  assert.equal((combo.match(/aria-haspopup="dialog"/g)||[]).length,2);
 } finally {await server.close();}
});

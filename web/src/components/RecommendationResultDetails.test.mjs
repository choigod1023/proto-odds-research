import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

test('historical detail reuses match header without replacing the archived pick',async()=>{
  const server=await createServer({configFile:false,esbuild:{jsx:'automatic'},server:{middlewareMode:true,hmr:false,watch:null},appType:'custom'});
  try {
    const {default:View}=await server.ssrLoadModule('/src/components/RecommendationResultDetails.jsx');
    const entry={id:'archive-1',sport:'bs',league:'KBO',home:'KIA',away:'LG',market:'승패',sel:'홈',odds:1.65,
      probability:0.6,market_prob:0.55,policy:'archived-policy',probability_source:'archived-model',
      kickoff_at:'2026-10-17T14:00:00+09:00',kickoff:Date.parse('2026-10-17T14:00:00+09:00'),
      published_at:'2026-10-17T12:00:00+09:00',recorded_at:'2026-10-17T12:01:00+09:00',
      outcome:{state:'hit',source:'official'},game:{year:2026,status:'정산',score:[4,2],prediction_record:{selection:'원정',probability:.99,odds:9}}};
    const render=e=>renderToStaticMarkup(createElement(View,{entry:e}));
    const html=render(entry);
    for(const text of ['경기 점수판','당시 보존된 추천 픽','60.0%','55.0%','1.65','archived-policy','2026-10-17 14:00 KST','4 : 2']) assert.ok(html.includes(text),text);
    assert.doesNotMatch(html,/99.0%|9.00|>원정</);
    const missing=render({...entry,probability:null,market_prob:undefined,odds:null,game:null});
    assert.match(missing,/경기 전 적중 확률 기록 없음/);assert.match(missing,/연결된 최종 점수 기록이 없습니다/);
    assert.doesNotMatch(missing,/NaN|Invalid Date|0.0%/);
    assert.match(render({...entry,game:{...entry.game,year:2025}}),/연결된 최종 점수 기록이 없습니다/);
    assert.match(render({...entry,game:{...entry.game,status:'진행'}}),/연결된 최종 점수 기록이 없습니다/);
  } finally {await server.close();}
});

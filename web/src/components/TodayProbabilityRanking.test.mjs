import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {selectionKey} from '../lib/unified-recommendation.js';

test('ranking shows probability, price, source and accessible detail action',async()=>{
  const server=await createServer({configFile:false,esbuild:{jsx:'automatic'},server:{middlewareMode:true,hmr:false,watch:null},appType:'custom'});
  try {
    const {default:View}=await server.ssrLoadModule('/src/components/TodayProbabilityRanking.jsx');
    const option={selection_id:'s',게임번호:'1',market:'승패',선택:'홈',배당:1.6};
    const game={year:2026,round:1,sport:'bs',league:'KBO',home:'홈팀',away:'원정팀',date:'09.22(화) 18:30',status:'경기전',options:[option]};
    const props={games:[game],now:Date.parse('2026-09-22T10:00:00+09:00'),memberships:new Map([[selectionKey(option,1),{recommended:true,selection:{predicted_hit_prob:.65}}]])};
    const html=renderToStaticMarkup(createElement(View,props));
    for (const word of ['1위','65.0%','1.60배','배당 기반 추정','aria-haspopup="dialog"','2폴 적중을 보장하지']) assert.ok(html.includes(word));
    assert.match(renderToStaticMarkup(createElement(View,{...props,stale:true})),/갱신이 지연/);
  } finally {await server.close();}
});

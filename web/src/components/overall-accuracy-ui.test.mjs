import assert from 'node:assert/strict';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

test('accuracy shows sports, both outcomes and dated saved picks without claiming popularity',async()=>{
 const server=await createServer({configFile:false,root:fileURLToPath(new URL('../../',import.meta.url)),esbuild:{jsx:'automatic'},optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,watch:null,hmr:false},appType:'custom'});
 try {
  const {default:Component}=await server.ssrLoadModule('/src/components/OverallAccuracy.jsx');
  const data={prediction_performance:{scope:'all_ledger_predictions',records:{one:{prediction_snapshot_id:'one',result:'hit',kickoff:'2026-10-18T08:00:00Z',captured_at:'2026-10-18T01:00:00Z',settled_at:'2026-10-18T11:00:00Z',fixture:{sport:'bs',league:'KBO',home:'KIA',away:'LG'},selection:'홈',market:'승패',odds:1.7},two:{prediction_snapshot_id:'two',result:'miss'}}}};
  const html=renderToStaticMarkup(createElement(Component,{data}));
  for(const text of ['야구','KIA vs LG','2026-10-18 17:00','2026-10-18 10:00','2026-10-18 20:00','종목 정보 없음','실패','확인할 수 없습니다','관심 픽만의 성적은 아닙니다'])assert.ok(html.includes(text),text);
  assert.ok(!html.includes('undefined'));
 } finally {await server.close();}
});

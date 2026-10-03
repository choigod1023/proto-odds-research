import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

test('successful old analysis remains visibly old despite fresh odds; failure is separate', async () => {
  const server = await createServer({configFile:false,optimizeDeps:{noDiscovery:true,include:[]},esbuild:{jsx:'automatic'},server:{middlewareMode:true,hmr:false,watch:null},appType:'custom'});
  try {
    const {SnapshotFreshnessView} = await server.ssrLoadModule('/src/components/SnapshotFreshness.jsx');
    const html = renderToStaticMarkup(createElement(SnapshotFreshnessView, {
      now: Date.parse('2026-10-01T12:00:00Z'), sources: [
        {name:'경기 분석',generatedAt:'2026-10-01T10:00:00Z',checked:true},
        {name:'배당',generatedAt:'2026-10-01T11:59:00Z',checked:true},
        {name:'추천',generatedAt:'2026-10-01T10:00:00Z',checked:true,error:true},
        {name:'점수',checked:false},
      ],
    }));
    assert.match(html, /120분 전 생성/); assert.match(html, /1분 전 생성/);
    assert.match(html, /최근 조회 성공/); assert.match(html, /최근 조회 실패 · 마지막 정상값 유지/);
    assert.match(html, /생성 시각 미확인/); assert.match(html, /조회 성공은 새 분석 완료를 뜻하지 않습니다/);
  } finally {await server.close();}
});

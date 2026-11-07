import assert from 'node:assert/strict';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

test('update notice renders KST source/freeze times and separate settlement guidance', async () => {
  const server = await createServer({configFile:false,
    root:fileURLToPath(new URL('../../', import.meta.url)), esbuild:{jsx:'automatic'},
    optimizeDeps:{noDiscovery:true, include:[]}, server:{middlewareMode:true, watch:null}, appType:'custom'});
  try {
    const {default: Notice} = await server.ssrLoadModule('/src/components/MatchUpdateNotice.jsx');
    const game = {year:2026, date:'11.07(토) 14:00', _liveOddsChanged:true,
      _liveOddsRecalculatedAt:'2026-11-07T03:00:00Z'};
    const render = props => renderToStaticMarkup(createElement(Notice, props));
    const html = render({game, now:Date.parse('2026-11-07T03:10:00Z'), phase:'upcoming'});
    assert.match(html, /갱신 및 정산 안내/);
    assert.match(html, /새 배당 판정 반영 대기/);
    assert.match(html, /12:00:00/);
    assert.match(html, /13:30:00/);
    assert.match(html, /약 1분 간격/);
    const finished = render({game:{status:'정산', prediction_record:{result:'pending'}}, phase:'finished'});
    assert.match(finished, /공식 상품 정산 대기/);
    assert.doesNotMatch(finished, /새 배당 판정 반영 대기/);
    assert.equal(render({game:{}, phase:'upcoming'}), '');
    const {readFile} = await import('node:fs/promises');
    const page = await readFile(new URL('../pages/Markets.jsx', import.meta.url), 'utf8');
    // One shared component is mounted outside the compact-row branch so both
    // the list and its original detail modal show the same explanation.
    assert.match(page, /<MatchUpdateNotice game=\{g\} phase=\{phase\}/);
  } finally {await server.close();}
});

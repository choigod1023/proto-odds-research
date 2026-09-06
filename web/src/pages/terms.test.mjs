import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { TERMS_ARTICLES, TERMS_META } from '../lib/service-terms.js';

test('terms show numbered articles, working contents and honest publication status without consent', async () => {
  const server = await createServer({ configFile:false,root:fileURLToPath(new URL('../../',import.meta.url)),
    esbuild:{jsx:'automatic'},optimizeDeps:{noDiscovery:true,include:[]},
    server:{middlewareMode:true,watch:null,hmr:false},appType:'custom' });
  try {
    const {default:Terms} = await server.ssrLoadModule('/src/pages/Terms.jsx');
    const html=renderToStaticMarkup(createElement(Terms));
    assert.equal(TERMS_ARTICLES.length,12);
    for(let i=1;i<=12;i++) {
      assert.match(html,new RegExp(`href="#article-${i}"`));
      assert.match(html,new RegExp(`id="article-${i}"`));
      assert.match(html,new RegExp(`제${i}조 \\(`));
    }
    assert.match(html,/<h1>서비스 이용약관<\/h1>/);
    assert.match(html,/검토본/); assert.match(html,/동의를 수집하지 않습니다/);
    assert.match(html,/정식 적용 시 별도 공지/);
    assert.equal(TERMS_META.operator,null); assert.equal(TERMS_META.contact,null); assert.equal(TERMS_META.effectiveAt,null);
    assert.doesNotMatch(html,/mailto:|type="checkbox"|<form/);
    assert.match(html,/법률상 책임을 배제하지 않습니다/);
    assert.match(html,/개인정보 처리방침.*대신하지 않습니다/);
    assert.match(html,/공식 판매처의 규정과 정산 결과/);
  } finally {await server.close();}
});

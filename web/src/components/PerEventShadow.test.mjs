import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

test('shadow view distinguishes abstention, pending, stale and unavailable data', async () => {
  const server = await createServer({configFile:false,esbuild:{jsx:'automatic'},server:{middlewareMode:true,hmr:false,watch:null},appType:'custom'});
  try {
    const {ShadowView} = await server.ssrLoadModule('/src/components/PerEventShadow.jsx');
    const render = props => renderToStaticMarkup(createElement(ShadowView, props));
    assert.match(render({loading:true}), /불러오는 중/);
    assert.match(render({error:true}), /0건이나 추천 없음으로 판단하지/);
    assert.match(render({}), /응답에 가상 비교 기록이 아직 없습니다/);
    const arm = {selected:1,settled:0,pending:1,roi:null,hits:0,misses:0,void:0};
    const payload = {generated_at:'2026-09-26T01:00:00+09:00',per_event_shadow:{policy:'v1',status:'recording',source_status:'fresh',observed_events:1,abstained_events:1,
      summary:{baseline:arm,challenger:{...arm,selected:0,pending:0},paired:{settled_events:0}},
      records:{x:{id:'x',baseline:{home:'A',away:'B',sel:'홈',market:'승패',odds:1.5},reason_counts:{unvalidated_probability:8}}}}};
    const html = render({payload,now:Date.parse(payload.generated_at)});
    assert.match(html, /검증된 확률·불확실성 근거 부족 8선택지/);
    assert.match(html, /산정 전/); assert.doesNotMatch(html, /0.00%/);
    assert.match(html, /미래 기대수익률이 아닙니다/);
    assert.match(render({payload,error:true,now:Date.parse(payload.generated_at)+3600000}), /마지막으로 받은 기록을 유지/);
    assert.match(render({payload,now:Date.parse(payload.generated_at)+3600000}), /오래되었거나 불완전한/);
    payload.per_event_shadow.summary.baseline = {...arm,settled:1,roi:-1,profit_units:-1};
    assert.match(render({payload}), /-100.00%/);
    payload.per_event_shadow.status = 'closed_settling';
    payload.per_event_shadow.capacity_reached = true;
    payload.per_event_shadow.summary.paired = {settled_events:1,profit_difference_units:1.5};
    const closed = render({payload});
    assert.match(closed, /신규 기록 종료/);
    assert.match(closed, /기록 상한에 도달/);
    assert.match(closed, /새 방식 − 기존 손익 차이 1.5단위/);
  } finally { await server.close(); }
});

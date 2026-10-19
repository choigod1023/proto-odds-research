export function officialScore(entry) {
  const evidence = entry.official_score;
  return evidence?.source === 'proto_full_game_result'
    && Date.parse(evidence.kickoff_at) === Date.parse(entry.kickoff_at)
    && Array.isArray(evidence.score) && evidence.score.length===2
    && evidence.score.every(n=>Number.isInteger(n) && n>=0) ? evidence.score : null;
}

export function resultExplanation(entry) {
  const score=officialScore(entry), result=entry.official_market_result;
  const names={승패:['홈','원정'],승무패:['홈','무','원정'],
    핸디캡:entry.n_way===3?['핸디홈','핸디무','핸디원정']:['핸디홈','핸디원정'],
    언더오버:['언더','오버'],홀짝:['홀','짝'],
    '승①패':['홈2+','1점차','원정2+'],'승⑤패':['홈6+','5점차이내','원정6+']}[entry.market];
  const three=names?.length===3;
  const winners=three?{홈승:0,핸디승:0,무승부:1,핸디무:1,'①':1,'⑤':1,홈패:2,핸디패:2}
    :{홈승:0,핸디승:0,언더:0,홀:0,홈패:1,핸디패:1,오버:1,짝:1};
  let winner=names?.[winners[result]], calculation='';
  if(score){
    const [h,a]=score, diff=h-a, sum=h+a;
    if(['승패','승무패'].includes(entry.market)) {
      const calculated=diff>0?'홈':diff<0?'원정':'무';
      if(!winner || winner===calculated) calculation=`홈 ${h} : 원정 ${a} → ${diff>0?'홈 승':diff<0?'원정 승':'무승부'}`;
      if(!winner && (diff!==0 || entry.market==='승무패')) winner=diff>0?'홈':diff<0?'원정':'무';
    } else if(['sc','bs','bk'].includes(entry.sport)) {
      const lineMatch=String(entry.market_label || '').match(/^(?:H|U)\s*([-+]?\d+(?:\.\d+)?)$/i);
      const line=lineMatch?Number(lineMatch[1]):null;
      // Explain arithmetic only when it agrees with the official settled choice.
      if(entry.market==='언더오버' && line!==null && sum!==line && winner===(sum<line?'언더':'오버'))
        calculation=`합계 ${h} + ${a} = ${sum}, 기준 ${line}${sum<line?' 미만':' 초과'}`;
      if(entry.market==='핸디캡' && line!==null){
        const adjusted=diff+line, calculated=adjusted>0?'핸디홈':adjusted<0?'핸디원정':'핸디무';
        if(winner===calculated) calculation=`홈 ${h}에 핸디캡 ${line>=0?'+':''}${line} 적용 → ${h+line} : ${a}`;
      }
    }
  }
  const expected=winner ? (winner===entry.sel?'hit':'miss') : null;
  if(expected && expected===entry.outcome.state)
    return `${calculation ? calculation+' · ' : ''}정산 선택 ${winner}, 당시 선택 ${entry.sel} → ${expected==='hit'?'적중':'실패'}`;
  return `${result ? '공식 마켓 결과 '+result+' · ' : ''}당시 선택 ${entry.sel}의 공식 정산은 ${entry.outcome.state==='hit'?'적중':'실패'}입니다. 점수만으로 판정 사유를 확정할 수 없어 공식 정산을 따릅니다.`;
}

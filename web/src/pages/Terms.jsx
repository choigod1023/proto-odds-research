import { Nav } from '../components/ui.jsx';
import { TERMS_ARTICLES, TERMS_META } from '../lib/service-terms.js';

export default function Terms() {
  return <main className="terms-page mx-auto max-w-[900px] px-5 pb-24" id="terms-top">
    <Nav current="terms.html" />
    <header className="terms-header">
      <p className="terms-eyebrow">PROODD · 서비스 정책</p>
      <h1>서비스 이용약관</h1>
      <p>서비스 이용에 관한 권리와 의무, 정보의 이용 조건을 안내합니다.</p>
      <div className="terms-meta"><span>버전 {TERMS_META.version}</span><span>작성일 <time dateTime={TERMS_META.revisedAt}>{TERMS_META.revisedAt}</time></span><span>{TERMS_META.status}</span></div>
    </header>
    <aside className="terms-publication" aria-label="약관 적용 상태">
      <strong>정식 적용 전 확인 안내</strong>
      <p>이 문서는 검토본입니다. 운영자 정보, 문의처 및 시행일을 확정한 후 정식 적용할 예정이며, 현재 페이지는 동의를 수집하지 않습니다.</p>
    </aside>
    <nav className="terms-contents" aria-label="약관 목차">
      <h2>목차</h2>
      <ol>{TERMS_ARTICLES.map(({ title }, index) => <li key={title}><a href={`#article-${index + 1}`}>제{index + 1}조 · {title}</a></li>)}</ol>
    </nav>
    <article aria-label="이용약관 본문">
      {TERMS_ARTICLES.map(({ title, clauses, important }, index) => <section key={title} id={`article-${index + 1}`} className={important ? 'terms-important' : undefined} aria-labelledby={`heading-${index + 1}`}>
        <h2 id={`heading-${index + 1}`}>제{index + 1}조 ({title})</h2>
        <ol>{clauses.map(clause => <li key={clause}>{clause}</li>)}</ol>
      </section>)}
      <section id="terms-addendum" aria-labelledby="terms-addendum-title">
        <h2 id="terms-addendum-title">부칙</h2>
        <p>시행일: {TERMS_META.effectiveAt || '정식 적용 시 별도 공지'}</p>
        <p>운영자: {TERMS_META.operator || '정식 적용 전 확인 예정'}<br />문의처: {TERMS_META.contact || '정식 적용 전 확인 예정'}</p>
      </section>
    </article>
    <a className="terms-back-top" href="#terms-top">맨 위로 이동 ↑</a>
  </main>;
}

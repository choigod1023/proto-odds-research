import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('corpus',Path(__file__).resolve().parents[1]/'scripts/collect_tipster_corpus.py')
corpus=importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)


class CorpusTests(unittest.TestCase):
    def test_listing_deduplicable_canonical_url(self):
        html='<a href="/analysis/123?page=2">[KBL] 2025년 1월 2일 홈 vs 원정</a><a href="/analysis/99">광고</a>'
        with patch.object(corpus,'get',return_value=html):
            rows=corpus.listing(('농구',1),Path('.'))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['url'],'https://www.wstv24.com/analysis/123')

    def test_cached_reparse_no_http_and_body_hash(self):
        html='<article><div class="view-nick"><a>분석가</a></div><span>2025-01-01 12:00</span><div id="board_view_con">승무패: 홈팀 승 핸디캡: +1.5</div></article>'
        row={'url':'https://www.wstv24.com/analysis/1','title':'2025년 1월 1일 홈 vs 원정','category':'농구'}
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);cache=out/'cache';cache.mkdir()
            cached=cache/(hashlib.sha256(row['url'].encode()).hexdigest()+'.html')
            cached.write_text(html,encoding='utf8')
            (out/'discovered.json').write_text(json.dumps([row]),encoding='utf8')
            with patch.object(corpus.requests,'get',side_effect=AssertionError('network forbidden')):
                corpus.reparse_cached(out)
            result=json.loads((out/'articles.json').read_text(encoding='utf8'))[0]
            self.assertEqual(result['author'],'분석가')
            self.assertEqual(result['body_sha256'],hashlib.sha256(result['body'].encode()).hexdigest())
            self.assertEqual(result['displayed_published'],'2025-01-01 12:00')

    def test_missing_body_rejected(self):
        with patch.object(corpus,'get',return_value='<article>not a full article</article>'):
            with self.assertRaisesRegex(ValueError,'missing'):
                corpus.article({'url':'https://www.wstv24.com/analysis/1'},Path('.'))


if __name__=='__main__': unittest.main()

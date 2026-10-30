import unittest
from scripts.prepare_proto_archive_inputs import merge_round_revisions


class CachedArchiveRevisionTests(unittest.TestCase):
    def test_later_round_replaces_all_rows_without_result_selection(self):
        old=[dict(year=2026,round=110,game_no='1',result='홈승'),dict(year=2026,round=110,game_no='2',result='홈패')]
        new=[dict(year=2026,round=110,game_no='1',result='취소')]
        meta=dict(season=2026,round=110,stored_at='2026-09-10T00:00:00Z')
        result,audit=merge_round_revisions(old,[(meta,new)],'2026-09-09T00:00:00Z')
        self.assertEqual(result,new)
        self.assertEqual(audit[0]['replaced_rows'],2)

    def test_older_cache_does_not_replace_newer_dataset(self):
        old=[dict(year=2026,round=110)]
        meta=dict(season=2026,round=110,stored_at='2026-09-08T00:00:00Z')
        result,audit=merge_round_revisions(old,[(meta,[dict(year=2026,round=110,result='changed')])],'2026-09-09T00:00:00Z')
        self.assertEqual(result,old)
        self.assertEqual(audit[0]['action'],'older_snapshot_not_used')

    def test_empty_cache_does_not_destroy_existing_round(self):
        old=[dict(year=2026,round=110)]
        meta=dict(season=2026,round=110,stored_at='2026-09-10T00:00:00Z')
        self.assertEqual(merge_round_revisions(old,[(meta,[])],'2026-09-09T00:00:00Z')[0],old)

    def test_time_offsets_are_compared_as_instants(self):
        old=[dict(year=2026,round=110)]
        meta=dict(season=2026,round=110,stored_at='2026-09-09T18:00:00+09:00')
        self.assertEqual(merge_round_revisions(old,[(meta,[dict(year=2026,round=110,result='changed')])],
                          '2026-09-09T11:47:00+00:00')[0],old)


if __name__=='__main__':unittest.main()

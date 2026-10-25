import copy
import unittest
from datetime import timedelta
from scripts.audit_recommendation_shadow import stamp
from scripts.run_forward_paper import advance, ticket_summary, POLICY
from tests.test_recommendation_shadow_audit import snapshot

NOW = stamp("2026-09-01T10:01:00+09:00")

def source(now=NOW):
    doc = snapshot(1)
    doc["generated_at"] = now.isoformat()
    for r in doc["recommendation_history"].values():
        r.update(policy=POLICY, result="unknown", settled_at=None,
                 recorded_at=(now-timedelta(minutes=1)).isoformat(),
                 published_at=(now-timedelta(minutes=1)).isoformat(),
                 kickoff_at=(now+timedelta(hours=4)).isoformat())
    return doc

class ForwardTests(unittest.TestCase):
    def test_republication_preserves_frozen_bet(self):
        doc=source(); events,_=advance([],doc,NOW); old=copy.deepcopy(events)
        for r in doc['recommendation_history'].values():
            r.update(recorded_at=(NOW+timedelta(hours=1)).isoformat(),
                     published_at=(NOW+timedelta(hours=1)).isoformat(),
                     result='hit', result_source='official',
                     settled_at=(NOW+timedelta(hours=6)).isoformat())
        done,_=advance(events,doc,NOW+timedelta(hours=7))
        self.assertEqual(done[:len(old)],old)
        self.assertEqual(ticket_summary(done,'probability-weekly2')['net'],23000)
        self.assertEqual(advance(done,doc,NOW+timedelta(hours=7))[1],[])

    def test_republication_does_not_relax_other_identity_fields(self):
        from scripts.run_forward_paper import settlement_row, identity
        r=next(iter(source()['recommendation_history'].values())); leg=identity(r)
        for key,value in [('odds',9),('probability',.9),('sel','other'),('market_label','other'),
                          ('kickoff_at',(NOW+timedelta(days=1)).isoformat()),('policy','other')]:
            changed=dict(r, published_at=NOW.isoformat()); changed[key]=value
            self.assertIsNone(settlement_row(leg,[changed],NOW+timedelta(days=2)))
        self.assertIsNone(settlement_row(leg,[r,r],NOW))
        self.assertIsNone(settlement_row(leg,[dict(r,published_at=(NOW+timedelta(days=2)).isoformat())],NOW))

    def test_future_only_idempotent(self):
        doc = source()
        events, added = advance([], doc, NOW, "hash")
        self.assertEqual(sum(e["type"]=="bet" for e in events),4)
        again, added = advance(events,doc,NOW,"hash")
        self.assertEqual(again,events)
        self.assertEqual(added,[])
        self.assertTrue(all(e["source_sha256"]=="hash" for e in events if e["type"]=="bet"))

    def test_stale_blocks_and_deduplicates(self):
        doc=source()
        events,_=advance([],doc,NOW+timedelta(days=1))
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["status"],"stale")
        self.assertEqual(advance(events,doc,NOW+timedelta(days=2))[1],[])

    def test_freeze_and_revision_match(self):
        doc=source()
        events,_=advance([],doc,NOW)
        old=copy.deepcopy(events)
        for r in doc["recommendation_history"].values():
            r.update(result="hit",result_source="official",
                     settled_at=(NOW+timedelta(hours=6)).isoformat())
        done,_=advance(events,doc,NOW+timedelta(hours=7))
        self.assertEqual(events,old)
        self.assertEqual(ticket_summary(done,"probability-weekly2")["net"],23000)
        doc["recommendation_history"]["0-0"]["odds"]=9
        mismatch,_=advance(events,doc,NOW+timedelta(hours=7))
        self.assertEqual(ticket_summary(mismatch,"probability-weekly2")["settled"],0)

    def test_pending_month_limit_and_rollover(self):
        events=[]
        for day in range(45):
            now=NOW+timedelta(days=day)
            events,_=advance(events,source(now),now)
        for account in ("probability-weekly2","probability-weekly3","ev_rank-weekly2","ev_rank-weekly3"):
            x=ticket_summary(events,account)
            self.assertEqual(x["bets"],5)
            self.assertEqual(x["pending_stake"],50000)

    def test_observed_loss_and_next_month(self):
        events=[]
        for day in range(45):
            now=NOW+timedelta(days=day)
            doc=source(now)
            for b in [e for e in events if e["type"]=="bet"]:
                for leg in b["legs"]:
                    row=dict(leg,result="miss",result_source="official",settled_at=now.isoformat())
                    doc["recommendation_history"][leg["kickoff_at"]+leg["id"]]=row
            events,_=advance(events,doc,now)
        # Month reset permits new tickets, but each placement month remains bounded.
        for account in ("probability-weekly2","probability-weekly3"):
            x=ticket_summary(events,account)
            self.assertTrue(all(v>=-50000 for v in x["monthly_net"].values()))
            self.assertGreater(x["bets"],5)

    def test_late_policy_and_result_reject(self):
        for mode in ("late","policy","result"):
            doc=source()
            for r in doc["recommendation_history"].values():
                if mode=="late":r["kickoff_at"]=(NOW+timedelta(minutes=29)).isoformat()
                if mode=="policy":r["policy"]="old"
                if mode=="result":r["result"]="hit"
            events,_=advance([],doc,NOW)
            self.assertFalse(any(e["type"]=="bet" for e in events))

    def test_unavailable_does_not_clear_pending(self):
        events,_=advance([],source(),NOW)
        after,_=advance(events,None,NOW+timedelta(minutes=30))
        self.assertEqual(ticket_summary(after,"probability-weekly2")["pending_stake"],10000)

    def test_future_settlement_is_not_observed(self):
        doc=source()
        events,_=advance([],doc,NOW)
        for r in doc["recommendation_history"].values():
            r.update(result="miss",result_source="official",settled_at=(NOW+timedelta(days=2)).isoformat())
        after,_=advance(events,doc,NOW+timedelta(days=1))
        self.assertEqual(ticket_summary(after,"probability-weekly2")["settled"],0)

    def test_same_event_no_pair(self):
        doc=source()
        doc["recommendation_history"]["0-1"]["home"]="0-0"
        events,_=advance([],doc,NOW)
        self.assertFalse(any(e["type"]=="bet" for e in events))

    def test_bad_protocol_or_clock_fails(self):
        events,_=advance([],source(),NOW)
        with self.assertRaises(ValueError):advance(events,source(),NOW-timedelta(seconds=1))
        events[0]["protocol"]="other"
        with self.assertRaises(ValueError):advance(events,source(),NOW)

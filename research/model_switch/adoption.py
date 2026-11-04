"""Decision checklist only: never authenticates sources or changes a model.

Inputs are computed report evidence supplied by an audited comparison pipeline.
Checking a flag here is not source authentication, and passing is not a release.
"""
import math


def recommendation(evidence):
    failures=[]
    def need(condition,reason):
        if not condition:failures.append(reason)
    def finite(x):return type(x) in (int,float) and math.isfinite(x)
    candidate=evidence.get('candidate')
    need(candidate in ('market','m0_actual'),'unsupported_or_current_candidate')
    references=('vote',) if candidate=='market' else ('vote','market')
    need(evidence.get('source_audit_status')=='verified_pregame_common','missing_pregame_source_audit')
    need(type(evidence.get('common_settled')) is int and evidence['common_settled']>=200,'first_review_requires_200_new_common_settled')
    need(type(evidence.get('distinct_observed_weeks')) is int and evidence['distinct_observed_weeks']>=8,'first_review_requires_8_observed_weeks')
    need(evidence.get('candidate_frozen_before_evaluation') is True,'candidate_not_frozen_before_new_evaluation')
    need(evidence.get('independent_prospective_evaluation') is True,'retrospective_or_reused_evaluation')
    windows=evidence.get('windows')
    valid_windows=isinstance(windows,list) and len(windows)>=2
    if valid_windows:
        from datetime import date
        try:
            ranges=[(date.fromisoformat(w['start']),date.fromisoformat(w['end'])) for w in windows]
            valid_windows=all(a<=b for a,b in ranges) and all(ranges[i][1]<ranges[i+1][0] for i in range(len(ranges)-1))
        except (KeyError,TypeError,ValueError):valid_windows=False
    need(valid_windows,'need_two_disjoint_predeclared_time_windows')
    need(evidence.get('evaluation_windows_predeclared') is True,'windows_not_predeclared')
    # Aggregate claims must describe the same two evaluation windows. This is
    # consistency checking, not authentication of the underlying observations.
    if valid_windows:
        from datetime import timedelta
        counts=0;weeks=set();valid_counts=True
        for w,(start,end) in zip(windows,ranges):
            daily=w.get('common_settled_by_date')
            if not isinstance(daily,dict) or not daily:valid_counts=False;continue
            for day,n in daily.items():
                try:d=date.fromisoformat(day)
                except (TypeError,ValueError):valid_counts=False;continue
                if not start<=d<=end or type(n) is not int or n<=0:valid_counts=False;continue
                counts+=n;weeks.add(d-timedelta(days=d.weekday()))
        need(valid_counts and counts==evidence.get('common_settled')
             and len(weeks)==evidence.get('distinct_observed_weeks'),'window_observation_counts_or_weeks_inconsistent')
    if valid_windows:
        for w in windows:
            for ref in references:
                x=w.get('roi_delta_vs_'+ref)
                need(finite(x) and x>0,'roi_advantage_missing_in_window_vs_'+ref)
    for ref in references:
        x=evidence.get('adjusted_roi_delta_lower_vs_'+ref)
        need(finite(x) and x>0,'uncertain_roi_advantage_vs_'+ref)
    x=evidence.get('adjusted_accuracy_delta_lower_vs_vote')
    need(finite(x) and x>=0,'accuracy_noninferiority_unproven')
    for metric in ('drawdown','lower_tail_loss','loss_capped_paper_loss'):
        x=evidence.get(metric+'_delta_vs_vote')
        need(finite(x) and x<=0,'risk_not_improved_'+metric)
    need(evidence.get('real_settlement_clocks_verified') is True,'unverified_settlement_clocks_or_exposure')
    need(evidence.get('runtime_and_source_tests_passed') is True,'runtime_source_verification_pending')
    return {'status':'eligible_for_human_release_review' if not failures else 'hold',
            'failure_reasons':sorted(set(failures)),'auto_switch':False,
            'release_authorization_required':True,'absolute_profit_proven':False,
            'scope':'decision checklist from caller evidence; not source authentication or future profit guarantee'}

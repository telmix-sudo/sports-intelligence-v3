"""Explicit approved-evidence doubles for tests of execution AFTER admission.

The actual fail-closed gate, chronology and frozen market comparisons are tested
without these doubles in test_admission_validation.py.
"""
from unittest.mock import patch


class ApprovedForward(dict):
    def get(self,key,default=None):
        return {'eligible_for_small_paper':True,'n_distinct_events':100,'days':20,'dates':20,
                'brier':.01,'market_brier':.8,'log_loss':.05,'market_log_loss':1.5,'blocks':[]}


def approved_research(test):
    return patch('sports_intelligence.research_pipeline.validation',new=lambda *args:ApprovedForward())(test)


class ApprovedOriginal:
    def observe(self,*args):pass
    def decision(self,*args):return {'eligible_for_paper':True,'basis':'SYNTHETIC_VERIFIED_MARKET_FIXTURE'}

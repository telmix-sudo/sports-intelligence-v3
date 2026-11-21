import unittest
from sports_intelligence.decision_review import MODEL_DIGEST, category, decision_key, first_decisions, forward_review

class DecisionReviewTests(unittest.TestCase):
    def test_first_frozen_choice_preserves_market_identity(self):
        row = dict(id="b", event_id="g", sport="epl", selection_index=0, market="STAT_1",
                   market_spec={"kind":"TOTAL","line":2.5},forecast_utc="2026-11-21T10:00:00+00:00")
        early = dict(row,id="a",forecast_utc="2026-11-21T09:30:00+00:00")
        other = dict(row,id="c",market_spec={"kind":"TOTAL","line":3.5})
        self.assertEqual(first_decisions([row,other,early],decision_key),[early,other])
        self.assertEqual(category(dict(classification="DAILY_EXPLORATORY_PAPER",admission_evidence={"eligible_for_paper":True})), "daily")
        self.assertEqual(category(dict(admission_evidence={"eligible_for_paper":True})), "validated")
        self.assertEqual(category({}), "other")

    def test_forward_cutoff_and_high_odds_control(self):
        row=dict(id="f",kind="FORECAST",model_code_sha256=MODEL_DIGEST,event_id="e",sport="epl",market="1X2",family="ELO_STANDARD",
                 forecast_utc="2026-11-21T09:00:00+00:00",start_utc="2026-11-21T12:00:00+00:00",
                 arms={"VALUE_GAP":1,"MARKET_FAVOURITE":0},odds=[1.8,3.0,4.0])
        settlement=dict(kind="SETTLEMENT",forecast_id="f",outcome=1,status="SETTLED")
        rows=[dict(row,id="old",event_id="prior",forecast_utc="2026-11-21T08:00:00+00:00"),row,settlement]
        summary=forward_review(rows)["football"]
        self.assertEqual((summary["events"],summary["settled"],summary["observed_only"]), (1,1,1))
        self.assertEqual((summary["original_units"],summary["filtered_units"],summary["market_units"]),(2,0,-1))

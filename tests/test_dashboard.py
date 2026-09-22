"""Dashboard reads production artifacts without creating decisions or spending credits."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard.service import Dashboard, ReadLedger, STRATEGY_DETAILS, ev_strategy_rows, signal, threshold_strategy_rows
from tracking import Ledger, Policy, utc


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'ledger.sqlite3'
        self.now = utc('2026-10-20T20:00:00Z')
        self.tip = utc('2026-10-20T23:00:00Z')
        self.policy = Policy('model')
        self.game = dict(id='0022600001', home='BOS', away='NYK', tipoff=self.tip, status='scheduled')
        self.forecast = dict(issued=self.now, probability=.7, payload=json.dumps({'tipoff_at':self.tip}))
        self.quote = dict(status='quoted', updated=self.now, tipoff=self.tip, home=1.8, away=2.1)

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_value_qualifies(self):
        s = signal(self.game,self.forecast,self.quote,{'status':'eligible'},self.policy,self.now)
        self.assertEqual(s['status'],'qualifies')
        self.assertEqual(s['selection'],'home')
        self.assertAlmostEqual(s['ev'],.26)

    def test_no_value_is_red_not_pending(self):
        f = {**self.forecast,'probability':.53}
        s = signal(self.game,f,self.quote,{'status':'eligible'},self.policy,self.now)
        self.assertEqual(s['status'],'no_bet')
        self.assertIsNone(s['selection'])

    def test_pending_guards(self):
        cases = [
            ({'status':'waiting'}, self.forecast,self.quote,True,'Waiting'),
            ({'status':'eligible'},self.forecast,{**self.quote,'updated':utc('2026-10-20T19:49:00Z')},True,'stale'),
            ({'status':'eligible'},{**self.forecast,'issued':utc('2026-10-18T20:00:00Z')},self.quote,True,'stale'),
            ({'status':'eligible'},self.forecast,None,True,'unavailable'),
            ({'status':'eligible'},self.forecast,self.quote,False,'verification'),
        ]
        for readiness, f, q, compatible, reason in cases:
            with self.subTest(reason=reason):
                out=signal(self.game,f,q,readiness,self.policy,self.now,compatible)
                self.assertEqual(out['status'],'pending')
                self.assertIn(reason,out['reason'])

    def test_cutoff_never_backfills(self):
        now=utc('2026-10-20T22:01:00Z')
        q={**self.quote,'updated':now}
        out=signal(self.game,self.forecast,q,{'status':'eligible'},self.policy,now)
        self.assertEqual(out['status'],'pending')
        self.assertIn('cutoff',out['reason'])

    def test_closed_never_qualifies(self):
        for status in ['started','final','canceled','postponed']:
            out=signal({**self.game,'status':status},self.forecast,self.quote,{'status':'eligible'},self.policy,self.now)
            self.assertEqual(out['status'],'pending')

    def seed(self):
        with Ledger(self.path) as ledger:
            details=dict(game_id=self.game['id'],home='BOS',away='NYK',model_id='model',
                         issued_at=self.now,tipoff_at=self.tip,home_win_probability=.7,
                         snapshot_hash='snapshot',input_features={})
            ledger.forecast(details,{'model_id':'model'},self.now)
            ledger.quote(self.game['id'],'draftkings',self.now,self.tip,1.8,2.1,self.now)
            with ledger.db:
                for team in ['BOS','NYK']:
                    ledger.db.execute('INSERT INTO snapshot_games VALUES(?,?,?,?,?)',('snapshot','previous',team,'2026-10-18',100))
            # Future quote and forecast must not leak into a point-in-time view.
            later=utc('2026-10-20T21:00:00Z')
            ledger.quote(self.game['id'],'draftkings',later,self.tip,2.5,1.5,later)
            ledger.forecast({**details,'issued_at':later,'home_win_probability':.9},{'model_id':'model'},later)

    def test_read_only_snapshot_and_cutoffs(self):
        self.seed()
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        service=Dashboard(self.path,Path(self.temp.name)/'absent.joblib')
        with patch.object(service,'model_info',return_value={'available':True,'compatible':True,'model_id':'model'}):
            data=service.overview(now=self.now)
        game=data['games'][0]
        self.assertEqual(game['signal']['status'],'qualifies')
        self.assertEqual(game['probability'],.7)
        self.assertEqual(game['odds']['home_decimal'],1.8)
        self.assertEqual(service.performance(self.policy.id,self.now)['decisions'],0)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        with ReadLedger(self.path) as ledger:
            with self.assertRaises(sqlite3.OperationalError):
                ledger.db.execute("DELETE FROM odds")

    def test_locked_card_uses_original_odds_and_forecast(self):
        self.seed()
        with Ledger(self.path) as ledger:
            quote=utc('2026-10-20T21:59:00Z')
            ledger.quote(self.game['id'],'draftkings',quote,self.tip,1.8,2.1,quote)
            ledger.decide(self.policy,'2026-10-20T22:00:00Z')
            later=utc('2026-10-20T22:10:00Z')
            ledger.quote(self.game['id'],'draftkings',later,self.tip,1.2,5.0,later)
        service=Dashboard(self.path,Path(self.temp.name)/'absent.joblib')
        with patch.object(service,'model_info',return_value={'available':True,'compatible':True,'model_id':'model'}):
            data=service.overview(now='2026-10-20T22:30:00Z')
        game=data['games'][0]
        self.assertEqual(game['signal']['label'],'Locked / qualifies')
        self.assertEqual(game['odds']['home_decimal'],1.8)
        self.assertEqual(game['probability'],.9)

    def test_missing_ledger_does_not_create_one(self):
        service=Dashboard(self.path,Path(self.temp.name)/'absent.joblib')
        overview=service.overview(now=self.now)
        self.assertFalse(overview['system']['ledger_available'])
        self.assertFalse(self.path.exists())
        self.assertEqual(overview['games'],[])

    def test_settled_performance_uses_frozen_decisions(self):
        self.seed()
        policy=replace(self.policy,version=1)
        with Ledger(self.path) as ledger:
            quote=utc('2026-10-20T21:59:00Z')
            ledger.quote(self.game['id'],'draftkings',quote,self.tip,1.8,2.1,quote)
            ledger.decide(policy,'2026-10-20T22:00:00Z')
            ledger.result(self.game['id'],110,100,observed='2026-10-21T02:00:00Z')
        service=Dashboard(self.path,Path(self.temp.name)/'absent.joblib')
        out=service.performance(policy.id,'2026-10-21T03:00:00Z')
        self.assertEqual(out['decisions'],1)
        value=next(r for r in out['summary'] if r['strategy']=='model_value')
        self.assertAlmostEqual(value['profit'],8)
        self.assertEqual(out['metrics'][0]['games'],1)

    def test_confidence_strategies_are_derived_from_locked_model_winner(self):
        import pandas as pd
        source = pd.DataFrame([dict(
            game_id='g1', strategy='model_winner', home='BOS', away='NYK',
            probability=.67, selection='home', odds=1.8, american_odds=-125,
            stake=10.0, profit=8.0, status='win',
            reason='higher_model_probability', settled_at='2026-10-21T02:00:00Z')])
        derived = threshold_strategy_rows(source)
        sixty = derived.loc[derived.strategy.eq('model_60')].iloc[0]
        seventy = derived.loc[derived.strategy.eq('model_70')].iloc[0]
        self.assertEqual(sixty.selection, 'home')
        self.assertEqual(sixty.stake, 10.0)
        self.assertEqual(seventy.status, 'skipped')
        self.assertEqual(seventy.stake, 0.0)
        self.assertEqual(source.iloc[0].strategy, 'model_winner')

    def test_strategy_metadata_explains_all_confidence_thresholds(self):
        self.assertIn('model_value', STRATEGY_DETAILS)
        for threshold in [55, 60, 65, 70, 75, 80, 85, 95]:
            detail = STRATEGY_DETAILS[f'model_{threshold}']
            self.assertIn(str(threshold), detail['rule'])

    def test_stricter_ev_strategies_reuse_the_locked_value_price(self):
        import pandas as pd
        source = pd.DataFrame([dict(
            game_id='g1', strategy='model_value', probability=.60,
            selection='home', odds=1.78, american_odds=-128,
            stake=10.0, profit=7.8, status='win', settled_at='2026-10-21T02:00:00Z')])
        derived = ev_strategy_rows(source)
        five = derived.loc[derived.strategy.eq('model_ev_5')].iloc[0]
        seven = derived.loc[derived.strategy.eq('model_ev_7')].iloc[0]
        self.assertEqual(five.selection, 'home')
        self.assertEqual(seven.status, 'skipped')
        self.assertEqual(seven.stake, 0.0)


if __name__=='__main__':
    unittest.main()

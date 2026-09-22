"""Local, read-only adapters. Never train, collect odds, or mutate the ledger."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys
import threading

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from budget import status as budget_status
from eligibility import EligibilityContext
from modeling import load_training_result, feature_importance_table
from production import load_production_config, production_model_path
from provenance import implementation_id, runtime_versions
from tracking import Ledger, Policy, DB_PATH, american_odds, probability_report, summarize, utc
from nba_api.stats.static.teams import get_teams

CONFIDENCE_THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.95)
EV_THRESHOLDS = (0.05, 0.07)
STRATEGY_DETAILS = {
    'favorite': dict(name='Market favorite', icon='landmark', rule='Bet the side with the shorter moneyline at the locked T-60 quote.', purpose='A market benchmark: can the model strategies beat simply backing the favorite?'),
    'home': dict(name='Always home', icon='house', rule='Bet the home team in every valid decision.', purpose='A control for the NBA home-court advantage, not a recommended wagering rule.'),
    'model_winner': dict(name='Model winner', icon='brain', rule='Bet whichever team the model places above 50%, even a 50.1% pick.', purpose='Measures raw winner-picking without asking whether the offered price is attractive.'),
    'model_value': dict(name='Model value (EV > 3%)', icon='badge-dollar-sign', rule='Bet only when model probability x decimal odds - 1 is strictly above 3%.', purpose='A price-aware rule with a small buffer for estimation error; 3% is a research threshold, not a promised return.'),
}
for _threshold in CONFIDENCE_THRESHOLDS:
    _key = f'model_{int(_threshold * 100)}'
    STRATEGY_DETAILS[_key] = dict(
        name=f'Model confidence >= {int(_threshold * 100)}%',
        icon='gauge',
        rule=f'Bet the model winner only when its win probability is at least {int(_threshold * 100)}%, regardless of price.',
        purpose='Tests accuracy filtering. A high-confidence favorite can still be a poor-value bet at expensive odds.')
for _threshold in EV_THRESHOLDS:
    _key = f'model_ev_{int(_threshold * 100)}'
    STRATEGY_DETAILS[_key] = dict(
        name=f'Model value (EV > {int(_threshold * 100)}%)',
        icon='badge-dollar-sign',
        rule=f'Bet the highest-EV side only when model probability x decimal odds - 1 is strictly above {int(_threshold * 100)}%.',
        purpose='Tests a stricter price-aware buffer using the same immutable T-60 forecast and quote.')


def clean(value):
    if isinstance(value, pd.DataFrame):
        return clean(value.to_dict('records'))
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def threshold_strategy_rows(rows):
    """Derive prespecified confidence strategies from immutable model-winner decisions."""
    if rows.empty:
        return rows.copy()
    base = rows[rows.strategy == 'model_winner']
    derived = []
    for threshold in CONFIDENCE_THRESHOLDS:
        frame = base.copy()
        frame['strategy'] = f'model_{int(threshold * 100)}'
        confidence = np.maximum(frame.probability, 1 - frame.probability)
        below = frame.selection.notna() & confidence.lt(threshold)
        frame.loc[below, ['selection', 'odds', 'american_odds', 'profit', 'settled_at']] = None
        frame.loc[below, 'stake'] = 0.0
        frame.loc[below, 'status'] = 'skipped'
        frame.loc[below, 'reason'] = f'model_confidence_below_{int(threshold * 100)}'
        derived.append(frame)
    return pd.concat(derived, ignore_index=True) if derived else rows.iloc[0:0].copy()


def ev_strategy_rows(rows):
    """Derive stricter EV thresholds from the stored model-value decision."""
    if rows.empty:
        return rows.copy()
    base = rows[rows.strategy == 'model_value']
    derived = []
    for threshold in EV_THRESHOLDS:
        frame = base.copy()
        frame['strategy'] = f'model_ev_{int(threshold * 100)}'
        selected_probability = np.where(frame.selection.eq('home'), frame.probability, 1 - frame.probability)
        expected_value = selected_probability * frame.odds - 1
        below = frame.selection.notna() & pd.Series(expected_value, index=frame.index).le(threshold)
        frame.loc[below, ['selection', 'odds', 'american_odds', 'profit', 'settled_at']] = None
        frame.loc[below, 'stake'] = 0.0
        frame.loc[below, 'status'] = 'skipped'
        frame.loc[below, 'reason'] = f'model_ev_below_{int(threshold * 100)}'
        derived.append(frame)
    return pd.concat(derived, ignore_index=True) if derived else rows.iloc[0:0].copy()


def dashboard_summary(rows, original):
    base = summarize(original)
    additions = []
    strategies = [*(f'model_{int(t * 100)}' for t in CONFIDENCE_THRESHOLDS),
                  *(f'model_ev_{int(t * 100)}' for t in EV_THRESHOLDS)]
    for strategy in strategies:
        frame = rows[rows.strategy == strategy].copy() if not rows.empty else rows.copy()
        if frame.empty:
            template = summarize(original.iloc[0:0])
            item = template[template.strategy == 'model_winner'].copy()
        else:
            frame['strategy'] = 'model_winner'
            item = summarize(frame)
            item = item[item.strategy == 'model_winner'].copy()
        item['strategy'] = strategy
        additions.append(item)
    return pd.concat([base, *additions], ignore_index=True)


class ReadLedger(Ledger):
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
        self.db.execute('BEGIN')


def signal(game, forecast, quote, eligibility, policy, now, compatible=True):
    """Preview of the pinned paper policy, never a stored decision or wager."""
    out = dict(status='pending', label='Pending', reason=None, selection=None, ev=None, edge=None)
    t = pd.Timestamp(now)
    reason = None
    if game['status'] != 'scheduled' or pd.Timestamp(game['tipoff']) <= t:
        reason = 'Game is closed'
    elif not compatible:
        reason = 'Model source or runtime needs verification'
    elif eligibility['status'] != 'eligible':
        reason = 'Waiting for preceding games' if eligibility['status'] == 'waiting' else 'Awaiting incorporated game data'
    elif not forecast:
        reason = 'No forecast for this model'
    elif t - pd.Timestamp(forecast['issued']) > pd.Timedelta(hours=policy.prediction_max_age_hours):
        reason = 'Forecast is stale'
    elif utc(json.loads(forecast['payload'])['tipoff_at']) != game['tipoff']:
        reason = 'Forecast schedule has changed'
    elif not quote or quote['status'] != 'quoted':
        reason = 'Moneyline odds unavailable'
    elif quote['tipoff'] != game['tipoff']:
        reason = 'Odds schedule has changed'
    elif t - pd.Timestamp(quote['updated']) > pd.Timedelta(minutes=policy.odds_max_age_minutes):
        reason = 'Odds are stale'
    elif t >= pd.Timestamp(game['tipoff']) - pd.Timedelta(minutes=policy.horizon_minutes):
        reason = 'Decision cutoff passed; see official history'
    if reason:
        out['reason'] = reason
        return out
    p, h, a = forecast['probability'], quote['home'], quote['away']
    evs = {'home': p * h - 1, 'away': (1 - p) * a - 1}
    side = max(evs, key=evs.get)
    market = (1 / h) / (1 / h + 1 / a)
    qualifies = evs[side] > policy.minimum_ev
    return dict(status='qualifies' if qualifies else 'no_bet',
                label='Meets criteria' if qualifies else 'No bet',
                reason='Current price passes the paper policy' if qualifies else 'Neither side exceeds the minimum expected return',
                selection=side if qualifies else None, ev=evs[side],
                edge=(p - market) if side == 'home' else (market - p))


class Dashboard:
    def __init__(self, db_path=DB_PATH, model_path=None):
        self.db_path = Path(db_path)
        self.model_path = Path(model_path) if model_path else production_model_path(load_production_config())
        self._model = None
        self._stamp = None
        self._lock = threading.RLock()

    def model(self):
        with self._lock:
            if not self.model_path.exists():
                return None
            stamp = self.model_path.stat().st_mtime_ns
            if stamp != self._stamp:
                self._model = load_training_result(self.model_path)
                self._stamp = stamp
            return self._model

    def model_info(self):
        r = self.model()
        if r is None:
            return dict(available=False, reason='Configured production artifact is missing', features=[], teams=[])
        meta = r.metadata
        compatible = meta.get('implementation_id') == implementation_id() and meta.get('runtime') == runtime_versions()
        table = feature_importance_table(r.deployment_model, r.feature_names)
        teams = []
        if r.latest_elos is not None:
            for abbreviation, row in r.latest_elos.iterrows():
                history = r.history[r.history.TEAM_ABBREVIATION.eq(abbreviation)].sort_values(['GAME_DATE', 'GAME_ID'])
                recent = history.tail(10)
                teams.append(dict(team=abbreviation, rank=int(row['RANK']), elo=float(row['ELO']),
                                  change=float(row['elo_change_last_5']),
                                  last_game=str(history.GAME_DATE.max().date()), wins=int(recent.WL.eq('W').sum()),
                                  losses=int(recent.WL.eq('L').sum())))
        return clean(dict(available=True, compatible=compatible, model_id=meta['model_id'],
                         name='Logistic regression', artifact=self.model_path.name, metadata=meta,
                         features=table, teams=teams, intercept=float(r.deployment_model.named_steps['logistic'].intercept_[0]),
                         historical_metrics=r.metrics))

    def overview(self, bookmaker='draftkings', now=None):
        now = utc(now)
        model = self.model_info()
        model_id = model.get('model_id', 'unavailable')
        policy = Policy(model_id, bookmaker=bookmaker)
        base = dict(as_of=now, model=model, policy={**asdict(policy), 'id': policy.id},
                    teams={t['abbreviation']: dict(name=t['full_name'], city=t['city'], nickname=t['nickname'], id=t['id']) for t in get_teams()},
                    games=[], policies=[], books=['draftkings', 'fanduel'],
                    system=dict(ledger_available=False, read_only=True, hosting='Local computer', scheduler='No recorded activity'))
        if not self.db_path.exists():
            return base
        with ReadLedger(self.db_path) as ledger:
            context = EligibilityContext(ledger, model_id, now)
            quotes = ledger.rows('''SELECT o.* FROM odds o WHERE bookmaker=? AND received<=? AND updated<=?
                AND id=(SELECT id FROM odds WHERE game_id=o.game_id AND bookmaker=? AND received<=? AND updated<=?
                ORDER BY received DESC,id DESC LIMIT 1)''', (bookmaker, now, now, bookmaker, now, now))
            odds = {q['game_id']: q for q in quotes}
            decisions = {d['game_id']: d for d in ledger.rows('SELECT * FROM decisions WHERE policy_id=? AND created<=?', (policy.id, now))}
            paper = ledger.paper_rows(policy.id, now)
            locked = {r['game_id']: r for r in paper.to_dict('records') if r['strategy'] == 'model_value'}
            firsts = {}
            for row in ledger.rows("SELECT * FROM eligibility WHERE model_id=? AND status='eligible' AND observed<=? ORDER BY observed,seq", (model_id, now)):
                payload = json.loads(row['payload'])
                firsts.setdefault((row['game_id'], payload.get('schedule_seq')), row['observed'])
            for gid, game in context.games.items():
                if game['tipoff'] < utc(pd.Timestamp(now) - pd.Timedelta(days=1)):
                    continue
                f, q = context.forecasts.get(gid), odds.get(gid)
                readiness = context.assess(gid)
                readiness['first_eligible_at'] = firsts.get((gid, game['schedule_seq']))
                preview = signal(game, f, q, readiness, policy, now, model.get('compatible', False))
                decision = decisions.get(gid)
                settled = locked.get(gid)
                if decision and settled and game['status'] == 'scheduled' and game['tipoff'] > now:
                    payload = json.loads(decision['payload'])
                    if payload['tipoff'] == game['tipoff'] and settled['status'] in ('pending', 'skipped'):
                        valid_skip = settled['status'] == 'skipped' and not payload['reason']
                        if settled['status'] == 'pending' or valid_skip:
                            saved_f = ledger.rows('SELECT * FROM forecasts WHERE id=?', (payload['prediction_id'],))
                            saved_q = ledger.rows('SELECT * FROM odds WHERE id=?', (payload['odds_id'],))
                            if saved_f and saved_q:
                                f, q = saved_f[0], saved_q[0]
                                side = settled['selection']
                                p = f['probability'] if side == 'home' else 1 - f['probability']
                                preview = dict(status='qualifies' if side else 'no_bet',
                                    label='Locked / qualifies' if side else 'Locked / no bet',
                                    reason='Official paper decision at the recorded cutoff', selection=side,
                                    ev=p * settled['odds'] - 1 if side else None, edge=None)
                base['games'].append(dict(id=gid, home=game['home'], away=game['away'], tipoff=game['tipoff'],
                    schedule_status=game['status'], schedule_observed=game['observed'],
                    probability=f['probability'] if f else None, forecast_at=f['issued'] if f else None,
                    forecast_id=f['id'] if f else None, forecast_count=None,
                    odds=self.quote_view(q), eligibility=readiness, signal=preview,
                    official=clean({**decisions[gid], 'payload': json.loads(decisions[gid]['payload'])}) if gid in decisions else None))
            base['games'].sort(key=lambda g: (g['tipoff'], g['id']))
            base['policies'] = [dict(id=p['id'], **json.loads(p['payload'])) for p in ledger.rows('SELECT * FROM policies')]
            base['books'] = sorted(set(base['books'] + [q['bookmaker'] for q in ledger.rows('SELECT DISTINCT bookmaker FROM odds')]))
            counts = {table: ledger.rows(f'SELECT COUNT(*) AS n FROM {table}')[0]['n'] for table in ('forecasts', 'odds', 'decisions', 'results')}
            slots = ledger.rows('SELECT game_id,observed,status,reason FROM scheduler_slots ORDER BY observed DESC LIMIT 15')
            requests = ledger.rows('SELECT started,finished,cost,status,purpose FROM api_requests ORDER BY id DESC LIMIT 10')
            base['system'].update(ledger_available=True, counts=counts, budget=budget_status(ledger, now),
                slots=slots, requests=requests, scheduler='Recorded activity; not a heartbeat' if slots else 'No recorded activity',
                latest_forecast=ledger.rows('SELECT MAX(received) AS stamp FROM forecasts')[0]['stamp'],
                latest_odds=ledger.rows('SELECT MAX(received) AS stamp FROM odds')[0]['stamp'])
        return clean(base)

    @staticmethod
    def quote_view(q):
        if not q:
            return None
        return dict(id=q['id'], bookmaker=q['bookmaker'], home_american=american_odds(q['home']) if q['home'] else None,
                    away_american=american_odds(q['away']) if q['away'] else None,
                    home_decimal=q['home'], away_decimal=q['away'], updated=q['updated'], received=q['received'], status=q['status'])

    def explain(self, forecast):
        r = self.model()
        if r is None or forecast['model_id'] != r.metadata['model_id']:
            return dict(available=False, reason='This forecast belongs to a different model version')
        values = json.loads(forecast['payload']).get('input_features', {})
        if set(values) != set(r.feature_names):
            return dict(available=False, reason='Saved input features are incomplete')
        x = pd.DataFrame([[values[c] for c in r.feature_names]], columns=r.feature_names)
        pipe = r.deployment_model
        z = pipe.named_steps['scale'].transform(x)[0]
        coef = pipe.named_steps['logistic'].coef_[0]
        probability = float(pipe.predict_proba(x)[0, 1])
        if not np.isclose(probability, forecast['probability'], atol=1e-9, rtol=0):
            return dict(available=False, reason='Saved probability does not match this artifact')
        rows = [dict(feature=c, value=values[c], standardized=float(z[i]), contribution=float(z[i] * coef[i])) for i,c in enumerate(r.feature_names)]
        rows.sort(key=lambda d: abs(d['contribution']), reverse=True)
        return dict(available=True, probability=probability, intercept=float(pipe.named_steps['logistic'].intercept_[0]), features=rows)

    def game(self, gid, bookmaker='draftkings', now=None):
        now = utc(now)
        if not self.db_path.exists():
            raise KeyError(gid)
        with ReadLedger(self.db_path) as ledger:
            games = ledger.rows('SELECT * FROM games WHERE id=?', (gid,))
            if not games:
                raise KeyError(gid)
            forecasts = ledger.rows('SELECT * FROM forecasts WHERE game_id=? AND received<=? AND issued<=? ORDER BY issued DESC,id DESC', (gid,now,now))
            quotes = ledger.rows('SELECT * FROM odds WHERE game_id=? AND bookmaker=? AND received<=? AND updated<=? ORDER BY received DESC,id DESC', (gid,bookmaker,now,now))
            schedules = ledger.rows('SELECT * FROM schedules WHERE game_id=? AND observed<=? ORDER BY observed DESC,seq DESC', (gid,now))
            results = ledger.rows('SELECT * FROM results WHERE game_id=? AND observed<=? ORDER BY observed DESC,seq DESC', (gid,now))
            decisions = ledger.rows('SELECT * FROM decisions WHERE game_id=? AND created<=? ORDER BY cutoff DESC', (gid,now))
            current = self.model()
            matching = [f for f in forecasts if current is not None and f['model_id']==current.metadata['model_id']]
            explanation = self.explain(matching[0]) if matching else dict(available=False, reason='No forecast for the configured model')
            return clean(dict(**games[0], forecasts=[{**f, 'payload':json.loads(f['payload'])} for f in forecasts],
                quotes=[self.quote_view(q) for q in quotes], schedules=schedules, results=results,
                decisions=[{**d,'payload':json.loads(d['payload'])} for d in decisions], explanation=explanation))

    def performance(self, policy_id=None, now=None):
        now = utc(now)
        model = self.model()
        default = Policy(model.metadata['model_id'] if model else 'unavailable')
        policy_id = policy_id or default.id
        if not self.db_path.exists():
            return dict(policy_id=policy_id, summary=[], metrics=[], calibration=[], rows=[], curve=[], decisions=0,
                        strategies=STRATEGY_DETAILS, stake_policy='Flat $10 per qualifying paper bet')
        with ReadLedger(self.db_path) as ledger:
            original = ledger.paper_rows(policy_id, now)
            metrics, calibration = probability_report(original)
            confidence = threshold_strategy_rows(original)
            value = ev_strategy_rows(original)
            derived = pd.concat([confidence, value], ignore_index=True)
            rows = pd.concat([original, derived], ignore_index=True) if not derived.empty else original
            curve = []
            if not rows.empty:
                for strategy, group in rows[rows.status.isin(['win','loss'])].groupby('strategy'):
                    group = group.sort_values(['settled_at','game_id'])
                    running = 0
                    for item in group.to_dict('records'):
                        running += item['profit']
                        curve.append(dict(strategy=strategy, date=item['settled_at'], profit=running))
            return clean(dict(policy_id=policy_id, summary=dashboard_summary(rows, original), metrics=metrics,
                calibration=calibration, rows=rows, curve=curve,
                decisions=int(original.game_id.nunique()) if not original.empty else 0,
                strategies=STRATEGY_DETAILS, stake_policy='Flat $10 per qualifying paper bet'))

    def elo(self, abbreviations):
        abbreviations = list(dict.fromkeys(abbreviations))
        r = self.model()
        known = set(r.history.TEAM_ABBREVIATION) if r is not None else set()
        if not abbreviations or len(abbreviations) > 10 or any(team not in known for team in abbreviations):
            raise KeyError(','.join(abbreviations))
        from features import add_team_features, build_matchup_frame
        from elo import add_elo_features
        team_games = add_team_features(r.history, rolling_window=r.rolling_window, min_periods=r.min_periods,
            rolling_history=r.rolling_history, use_prior_season_features=r.use_prior_season_features, prior_decay_games=r.prior_decay_games)
        matchups = build_matchup_frame(team_games, rolling_window=r.rolling_window, require_features=False)
        frame, latest = add_elo_features(matchups, k_factor=r.elo_k, playoff_k_factor=r.elo_playoff_k,
                                        home_advantage=r.elo_home_advantage, carryover=r.elo_carryover)
        output = {team: [] for team in abbreviations}
        for team in abbreviations:
            if not np.isclose(latest.loc[team,'ELO'], r.latest_elos.loc[team,'ELO']):
                raise ValueError('Replayed Elo differs from the production snapshot')
        selected = frame.loc[frame.HOME_TEAM.isin(abbreviations)|frame.AWAY_TEAM.isin(abbreviations)]
        for row in selected.itertuples():
            k = r.elo_playoff_k if row.IS_PLAYOFFS and r.elo_playoff_k is not None else r.elo_k
            home_delta = k * (row.HOME_WIN - row.elo_expected_home_win)
            for team, pre, delta in ((row.HOME_TEAM, row.home_elo_pre, home_delta),
                                     (row.AWAY_TEAM, row.away_elo_pre, -home_delta)):
                if team in output:
                    output[team].append(dict(date=str(pd.Timestamp(row.GAME_DATE).date()), game_id=row.GAME_ID,
                                             elo=pre+delta, change=delta))
        return clean(dict(series=[dict(team=team, points=output[team]) for team in abbreviations]))

    def team(self, abbreviation):
        r = self.model()
        if r is None or abbreviation not in set(r.history.TEAM_ABBREVIATION):
            raise KeyError(abbreviation)
        points = self.elo([abbreviation])['series'][0]['points']
        history = r.history[r.history.TEAM_ABBREVIATION.eq(abbreviation)].sort_values(['GAME_DATE','GAME_ID'],ascending=False).head(20)
        fields = [c for c in ['GAME_DATE','MATCHUP','WL','PTS','PLUS_MINUS'] if c in history]
        return clean(dict(team=abbreviation, points=points, recent=history[fields]))

    def research(self):
        path = ROOT / 'reports/experiments/2026-09-21-research-summary/outer_results.csv'
        return clean(pd.read_csv(path)) if path.exists() else []

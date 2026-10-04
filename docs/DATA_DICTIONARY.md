# Data Dictionary

The source of truth is `data/tracking/predictions.sqlite3`. It is ignored by Git
and persisted as a compressed private R2 object in the hosted design.

| Table | Grain | Important fields |
|---|---|---|
| `games` | One NBA game ID | home, away |
| `schedules` | One observed schedule revision | tipoff UTC, status, observed UTC, source |
| `models` | One immutable model ID | metadata JSON with source/runtime/snapshot identity |
| `forecasts` | One received probability snapshot | game/model, issue and receipt times, probability, exact inputs |
| `odds` | One bookmaker observation | update/receipt/tipoff UTC, home/away decimal price, provider payload |
| `results` | One observed result revision | status, scores, observation time, source |
| `snapshot_games` | Game membership in a forecast data snapshot | snapshot hash, team, date, points |
| `eligibility` | One changed readiness state | model/game, status, observed time, compact references |
| `eligibility_blocker_sets` | One deduplicated blocker set | content-addressed ID |
| `eligibility_blockers` | One prior-game blocker in a set | team, game, tipoff, status, position |
| `policies` | One immutable paper-policy version | serialized policy settings |
| `decisions` | One policy/game cutoff decision | cutoff, creation time, frozen forecast/quote references, bets |
| `scheduler_slots` | One game/model/horizon attempt | due context, status, reason |
| `api_requests` | One reserved provider request | cost, purpose, status, provider quota headers |
| `quota_cycles` | One provider billing cycle | reset, baseline use, allowance |
| `worker_runs` | One hosted invocation | start/finish, health, version, publish, next wake |
| `notification_deliveries` | One recipient/event/channel | recipient hash, status, provider ID, timestamps |
| `actual_wagers` | One manually imported accepted receipt | selection, accepted price/stake, external reference |
| `actual_settlements` | One manually imported receipt settlement | status, payout, observation time |

All event, issue, update, receipt, cutoff, and observation timestamps are stored
as explicit UTC ISO-8601 values. The UI translates them without discarding UTC.
American odds are a display conversion; decimal odds remain the calculation and
persistence format.

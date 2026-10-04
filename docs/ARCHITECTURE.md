# Courtside Architecture

Courtside separates model research, immutable prospective evidence, collection,
and presentation. The browser never receives provider keys, R2 credentials, the
raw SQLite ledger, or a model artifact.

```text
NBA stats + schedule                 The Odds API
          |                                |
          +---------- Cloud Run worker ----+
                           |
                 quota-aware T-6h/T-60
                           |
               append-only SQLite ledger
                           |
                 gzip checkpoint in R2
                           |
              bounded static JSON export
                           |
                 Cloudflare Pages / PWA
```

## Components

| Component | Responsibility | Mutates evidence? |
|---|---|---:|
| `src/modeling.py`, `features.py`, `elo.py` | Train and evaluate the probability model | No ledger writes |
| `src/scheduler.py` | Refresh completed history and run bounded collection slots | Yes, append-only records |
| `src/tracking.py` | Schema, decisions, settlement, quota, heartbeats, alert audit | Yes |
| `dashboard/service.py` | Point-in-time read adapter | No |
| `dashboard/export_static.py` | Publish bounded JSON and the static shell | No |
| `hosted/worker.py` | Restore, run, checkpoint, notify, and publish | Orchestrates writes |
| Cloudflare R2 | Durable compressed ledger and verified runtime bundle | Object replacement |
| Cloudflare Pages | Public or Access-protected read-only product | Published snapshot only |

## Failure Boundaries

- Cloud Run is configured with one instance and concurrency one. The SQLite
  scheduler lease supplies a second overlap guard.
- A completed collection checkpoint reaches R2 before Pages deployment. A Pages
  failure therefore cannot erase quota use, an odds observation, or a decision.
- Every notification has a deterministic event key and recipient hash. The
  recipient address exists only in a cloud secret.
- Runtime archives contain only the pinned production model, its manifest, and
  cached public NBA history. Extraction rejects absolute paths and traversal.
- Pages republishes when evidence changes or the snapshot reaches six hours old,
  keeping normal usage well below the free monthly build allowance.
- Model identity hashes only probability-producing modules. Dashboard, ledger,
  and scheduler maintenance cannot silently change the estimator identity.

## Trust Boundary

Courtside is a decision-support and paper-research product. It does not accept
sportsbook credentials, automate a sportsbook login, or submit wagers. A future
execution connector would require an authorized API, jurisdiction controls,
limits, reconciliation, and a separate security review.

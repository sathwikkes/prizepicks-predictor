# Prospective prediction tracking

Historical replay proves the machinery; only predictions saved before tip-off prove the
system. This is the workflow for the latter.

## Data flow

```
ppp ingest-sdv --season 2027        box scores (NBA.com via SportsDataverse)  -> games, player_game_stats
ppp ingest-schedule --season 2027   ESPN schedule (SportsDataverse)           -> scheduled_games
ppp forecast --date D --save        pregame forecasts                         -> prediction_snapshots (immutable)
ppp evaluate-predictions --through D  after the games                         -> prediction_outcomes
ppp monitor                          report
```

## Tables

* `scheduled_games`: one row per schedule entry with UTC tip-off, status
  (`scheduled | in_progress | final | postponed | canceled | unknown`) and scores. Mutable;
  reloads update it.
* `prediction_snapshots`: one row per (player, target date, stat, model, line, revision).
  Holds the cutoff and generation time (UTC), tip-off, model and calibration versions,
  projection, interval, raw and calibrated probabilities, sample size, data freshness,
  the full feature set as JSON and the warnings. **Immutable**: the ORM refuses updates
  (`SnapshotImmutableError`), and evaluation never writes to it.
* `prediction_outcomes`: at most one row per snapshot, written after the game. Holds the
  actual value and minutes, errors, interval coverage, the More/Less/Push outcome and
  Brier/log loss for raw and calibrated probabilities.

## Forecast rules

* Cutoff = UTC time of generation; it is stored with every snapshot.
* A game is forecast only if its status is `scheduled` and its tip-off is after the
  cutoff. Unknown tip-off: only if the game date is after the cutoff date. Started,
  final, postponed and canceled games are skipped and counted.
* Participants are **expected**, not confirmed: players whose latest stored game in the
  last 21 days was for a team playing that day. No lineups, injury reports or game-day
  minutes are used; none are available before tip-off in this system.
* History is strictly before the target date (same-day earlier games are excluded).
* Lines are synthetic trailing-10 medians unless a manual targets file supplies them;
  the snapshot records `line_source`.
* Idempotent: an identical forecast (same player, date, stat, model, line, revision) is
  reported as a duplicate and not re-inserted. `--revise` saves revision n+1 instead.

## Evaluation rules

| Outcome status | Condition | Final? |
| --- | --- | --- |
| `evaluated` | the player's box row exists with minutes > 0 | yes |
| `did_not_play` | the game's box score is loaded but the player has no row or 0 minutes | yes |
| `voided` | the scheduled game is postponed or canceled | yes |
| `missing_result` | no completed game found yet | no, re-evaluated on later runs |

A DNP is never scored as an under. A push (actual equals an integer line) is recorded and
excluded from Brier and log loss. Snapshots whose tip-off is still in the future are
left pending.

## Monitoring

`ppp monitor` reports counts by status, MAE/RMSE/bias, interval coverage, Brier, log loss
and ECE for raw and calibrated probabilities, reliability bins, and a cluster bootstrap
(resampling player-games, not rows) for the calibrated-minus-raw Brier difference.
Results under 200 evaluated predictions are labelled preliminary. Hit rates against
synthetic lines are not market results and are never reported as such.

## Status on 2026-11-03

* Operational: schedule ingestion (ESPN, refreshed daily by SportsDataverse), box-score
  ingestion (about one day behind live games), forecast generation, evaluation, monitoring.
* First genuine prospective batch: 1,191 snapshots for the 14 games of 2026-11-04,
  generated 2026-11-03 05:15 UTC, earliest tip-off 2026-11-05 00:00 UTC. Evaluate with
  `ppp ingest-sdv --season 2027 --force-download` then `ppp evaluate-predictions
  --through 2026-11-04` once the box scores are published.
* Not available: confirmed active rosters, injury reports with timestamps, real lines.
  Participant lists will include some players who end up inactive; those evaluate as
  `did_not_play`, which is the honest outcome.
* Scheduling: nothing runs automatically. A daily cron (ingest-sdv, ingest-schedule,
  forecast for tomorrow, evaluate through yesterday) is the natural next step once the
  licensing question is settled.

# Data quality report: SportsDataverse NBA import

Generated 2026-11-02 from `ppp data-quality` after `ppp ingest-sdv --season 2024 --season 2025 --season 2026 --season 2027`
(regular season only, DNP rows excluded). Reproduce with those two commands.

## Totals

| Metric | Value |
| --- | --- |
| Games | 3,778 |
| Player-game records | 81,348 |
| Unique players | 854 |
| Teams | 30 |
| Latest game date | 2026-10-31 |
| Source | `sportsdataverse_nba_stats` (NBA.com box scores republished by SportsDataverse) |

## Season coverage

| Season | Type | Games | Player-games | Players | First | Last | Complete? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2023-24 | regular | 1,230 | 26,401 | 572 | 2023-10-24 | 2024-04-14 | yes (82 × 30 / 2) |
| 2024-25 | regular | 1,230 | 26,306 | 569 | 2024-10-22 | 2025-04-13 | yes |
| 2025-26 | regular | 1,230 | 26,651 | 582 | 2025-10-21 | 2026-04-12 | yes |
| 2026-27 | regular | 88 | 1,990 | 441 | 2026-10-20 | 2026-10-31 | in progress |

## Integrity checks

| Check | Result |
| --- | --- |
| Duplicate (game, player) keys | 0 |
| Duplicate games (date, home, away) | 0 |
| Stat rows without a game date | 0 |
| Games without final scores | 0 |
| Records rejected by validation | 0 |
| Rows whose team was not in its game (adapter check) | 0 |
| Zero-minute rows stored | 15 (players credited with `0:00`, e.g. a sub who checked in at the buzzer; one recorded a rebound). Excluded from all features. |
| Players appearing for more than one team | 404 across four seasons (trades and free agency). One `person_id` → one player row; team is recorded per game. |
| Missing optional values | steals, blocks, turnovers, 3PM, started: 0 missing. `started` is derived from NBA.com's `position` field, which is set for exactly 5 players per team per game. |

## What was skipped at ingestion, and why

| Season | Box rows | Stored | DNP / DND / NWT / Inactive (no minutes) | Playoffs | Play-in | Other (Cup final) |
| --- | --- | --- | --- | --- | --- | --- |
| 2023-24 | 34,843 | 26,401 | 5,984 | 2,270 | 160 | 28 |
| 2024-25 | 35,114 | 26,306 | 6,209 | 2,413 | 159 | 27 |
| 2025-26 | 34,786 | 26,651 | 5,528 | 2,408 | 169 | 30 |
| 2026-27 | 2,767 | 1,990 | 777 | 0 | 0 | 0 |

Every excluded row is accounted for: `stored + dnp + playoffs + playin + other == box rows`.
Playoffs can be loaded with `--season-types regular,playoffs`. Play-in and Cup-final rows
have no team-log row (hence no date) and are rejected with a stated reason if requested.

## Idempotency

A second run over the same files reported `inserted=0 updated=81,348` (it backfilled the
new `started` flag) and a third run reported `inserted=0 updated=0 unchanged=81,348`.

## Cross-check against an independent source

300 stored 2024-25 regular-season rows were sampled at random and matched to
SportsDataverse's **ESPN** box scores by player name and game date.

| Result | Count |
| --- | --- |
| Matched by name and date | 288 |
| Points, rebounds and assists identical | 288 of 288 |
| Minutes within 1.0 of ESPN's rounded value | 288 of 288 |
| Unmatched | 12, all names with diacritics that ESPN spells differently (Jokić, Valančiūnas, Nurkić, Bogdanović, Matković, Diabaté) |

No stat discrepancies were found. An earlier 12-row spot check also matched exactly. No
stored values were modified as a result of the comparison.

## Provenance

| Field | Value |
| --- | --- |
| Ingestion runs | 9, all `succeeded` (4 initial loads, 4 backfill updates, 1 no-op) |
| Release freshness | `nba_stats_player_boxscores/timestamp.json`: 2026-11-01 09:04 EST; 2027 asset last-modified 2026-11-01 14:04 UTC |
| Local cache | `data/raw/sportsdataverse/` (gitignored), 19.8 MB |
| Record-level provenance | every `player_game_stats` row points to its `ingestion_runs` row; every player and game carries its NBA.com id in `external_ids` |

## Caveats

* Synthetic fixture data (`data/samples/`) was **not** loaded into this database. Keep it that way for any evaluation.
* "Complete" above means the expected number of regular-season games is present, not that NBA.com's own stat corrections have been re-fetched since publication.
* The 2026-27 season is partial and will lag live games by roughly a day.

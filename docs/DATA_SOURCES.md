# Data source evaluation

Last reviewed: 2026-11-02. Details change; verify before relying on them.

## Decision (Phase 2.5)

**Connected and loaded: SportsDataverse public GitHub releases** of NBA.com box scores.
Four regular seasons (2023-24, 2024-25, 2025-26 and the in-progress 2026-27) are imported
through `ingestion/sportsdataverse.py` with the `ppp ingest-sdv` command. No API key, no
payment, no scraping: the files are plain CSV release assets downloaded over HTTPS.

**Kept as an independent cross-check:** SportsDataverse's ESPN-derived player box scores
(`espn_nba_player_boxscores`). See `docs/DATA_QUALITY_REPORT.md`.

**Not used:** anything that scrapes stats.nba.com directly (`nba_api`), Basketball
Reference, or private PrizePicks endpoints.

## SportsDataverse: what was verified

Repository: https://github.com/sportsdataverse/sportsdataverse-data (MIT license for the
repository and tooling; the statistics themselves are NBA.com data and ESPN data, used here
for research). Release assets are named `{dataset}_{season}.csv|parquet` where `season` is
the season's **end** year (2025 = 2024-25).

| Release tag | Actual content | Rows / season | Freshness seen |
| --- | --- | --- | --- |
| `nba_stats_player_boxscores` → `player_boxscores_{season}.csv` | **Player** box scores from NBA.com: `person_id`, names, `team_tricode`, `side` (home/away), `minutes` as `MM:SS`, pts/reb/ast and the rest, `game_id`, `season_type_id`, `comment` for DNP. **No game date, no opponent.** `position` is filled for exactly the five starters. | ~35,000 | 2027 file updated 2026-11-01; `timestamp.json` says 2026-11-01 09:04 EST |
| `nba_stats_player_game_logs` → `player_game_logs_{season}.csv` | **Team** game logs despite the tag name: `game_id`, `team_id`, `game_date`, `matchup`, `season_type`, team `pts`. 2 rows per game. Covers regular season + playoffs only (no play-in, no NBA Cup final). | 2,628 | 2027 file updated 2026-11-01 |
| `espn_nba_player_boxscores` → `player_box_{season}.csv` | ESPN player box scores: `athlete_id`, `game_date`, `home_away`, opponent, `did_not_play`, `starter`, scores. Minutes are **whole numbers**. Team abbreviations differ from NBA.com (GS, NO, SA, WSH). | ~35,000 | 2027 file updated 2026-11-01 |

The adapter joins the first two on `(game_id, team_id)`. The other team's row in the same
game gives the opponent and final score. Verified on 2024-25: every regular-season and
playoff box row joins; the 186 unjoinable rows were all play-in (`005…`) and NBA Cup final
(`006…`) games, which are rejected with a reason when requested.

Why NBA.com-stats over ESPN as the primary: official `person_id`s (the key other
`nba_stats_*` releases such as rosters, schedules and injuries will share), exact `MM:SS`
minutes for per-minute rates, and the stat lines PrizePicks itself settles against.

### Known limitations

* Dates come from the team-log file, so game types missing there (play-in, Cup final)
  cannot be dated. `nba_stats_schedules` could fill that gap later.
* DNP rows carry a `comment` but no reason code taxonomy; the adapter records the prefix
  (DNP / DND / NWT / Inactive) in its run statistics. They are excluded from storage by
  default (`--include-dnp` stores them as 0-minute rows).
* Freshness depends on SportsDataverse's schedule. The 2026-27 file lagged live games by
  about a day at review time. Check `timestamp.json` under each release.
* No injury reports, no lineups, no pace or defensive ratings yet. `nba_stats_rosters`,
  `nba_stats_schedules` and `nba_stats_team_boxscores` exist in the same repository and
  are the natural next adapters.
* No PrizePicks lines, historical or live.

## Other providers considered

| Provider | Verdict |
| --- | --- |
| **BALLDONTLIE** | Documented API, but per-game stats sit behind a paid tier. Adapter exists (`ingestion/balldontlie.py`, mock-tested only). Not needed now that SportsDataverse covers game logs. |
| **`nba_api` / stats.nba.com** | Unofficial wrapper over undocumented endpoints with aggressive throttling and no granted API access. Excluded as a product dependency. SportsDataverse already republishes the same data. |
| **NBA live box-score CDN endpoints** | Could give same-day updates before SportsDataverse refreshes. Undocumented; revisit only if daily latency becomes a real problem. |
| **The Odds API** | No player stats; some sportsbook player props. Possible later benchmark for market-implied probabilities. PrizePicks not covered. |
| **Basketball Reference** | Terms prohibit scraping. Excluded. |
| **Kaggle dumps** | Licensing varies, freshness poor. Loadable through the CSV adapter if a specific dataset's license allows. |
| **SportsDataIO, Sportradar** | Enterprise pricing. Reachable later through the adapter pattern. |

## Adding a provider

Implement `GameLogProvider` (`ingestion/base.py`): a `source_name`, `describe()`, and
`iter_game_logs()` yielding `NormalizedGameLog` or `RecordValidationError`, raising
`ProviderError` for whole-run failures. Loader, query layer and prediction engine need no changes.

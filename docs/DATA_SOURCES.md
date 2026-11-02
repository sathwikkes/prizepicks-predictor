# Data source evaluation

Last reviewed: 2026-11-02. Pricing and tier details change; verify on the provider's site
before relying on them.

## Decision for Phase 1

**Connected:** CSV ingestion with a committed, clearly labeled synthetic fixture
(`data/samples/synthetic_game_logs.csv`). This exercises every layer without a paid API.

**Implemented but not live-verified:** a BALLDONTLIE adapter
(`ingestion/balldontlie.py`) written against the documented v1 `/teams` and `/stats`
response shapes and covered by tests using a mocked HTTP transport. No API key is
configured in this repository, so it has not been run against the real service.

**Not used:** anything that scrapes stats.nba.com or relies on undocumented PrizePicks
endpoints.

## Providers considered

| Provider | Game logs | Cost | Notes |
| --- | --- | --- | --- |
| **BALLDONTLIE** (api.balldontlie.io) | Yes (`/stats`, `/box_scores`) | Free tier covers teams/players/games only. Per-game player stats require a paid tier (ALL-STAR, roughly $10/mo at last check); injuries, advanced stats and odds are on a higher tier. | Documented REST API, API-key auth, cursor pagination, data back to 1946 for basic stats. Rate limits scale by tier. Terms permit personal/analytics use. Best candidate for a low-cost first live source. |
| **NBA Stats (stats.nba.com) via `nba_api`** | Yes, very rich | Free | Unofficial wrapper over undocumented endpoints; headers must mimic a browser, requests are throttled unpredictably, and the NBA's terms do not grant API access. Not a stable or clearly authorized foundation for a product. Excluded. |
| **The Odds API** | No player stats. Player prop odds for some books; PrizePicks is not a sportsbook and is not covered. | Free tier 500 requests/mo; historical odds are paid. | Useful later for market-implied probabilities as a benchmark, not for stats. |
| **Basketball Reference** | Yes (HTML tables) | Free | Terms of use prohibit automated scraping. Excluded. |
| **Kaggle / public CSV dumps** | Historical only | Free | Licensing varies per dataset and freshness is poor. Acceptable for one-off backtest data if the specific dataset's license allows; load via the CSV adapter. |
| **SportsDataIO, Sportradar** | Yes, with injuries and projections | Enterprise pricing / trial keys | High quality but outside the current budget. Adapter pattern keeps them reachable later. |
| **PrizePicks projections** | n/a | n/a | No public API or documented data license. PrizePicks lines are entered manually (Phase 5). We do not call private endpoints. |

## What a live source needs to provide for Phase 2+

* Per-player, per-game minutes, points, rebounds, assists with game date and opponent.
* Stable player and team identifiers.
* Game results and schedule, for rest-day and back-to-back features.
* Injury/availability reports with an "as-of" timestamp (needed to avoid leakage).
* Historical prop lines with an availability timestamp (needed for honest wagering backtests).

BALLDONTLIE's paid tiers cover the first three. Timestamped injury data and historical
PrizePicks lines are not available from any provider evaluated here; the roadmap treats
wagering backtests as conditional on obtaining them.

## Adding a provider

Implement `GameLogProvider` (`ingestion/base.py`): a `source_name`, a `describe()`, and an
`iter_game_logs()` that yields `NormalizedGameLog` records or `RecordValidationError` for bad
rows, raising `ProviderError` for whole-run failures. The loader and query layer need no changes.

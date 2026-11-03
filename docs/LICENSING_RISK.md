# Data licensing and provenance: technical risk assessment

Written 2026-11-02. This is an engineering risk note, not legal advice. Nothing here has
been reviewed by a lawyer.

## What we actually have rights to

| Layer | Source | License / terms | What it covers |
| --- | --- | --- | --- |
| Our code | this repository | MIT | the software |
| SportsDataverse tooling and release packaging | github.com/sportsdataverse/sportsdataverse-data | MIT (repository `LICENSE`) | the scripts, the file layout, the act of redistribution by SportsDataverse |
| The statistics themselves | NBA.com / NBA Stats (collected by SportsDataverse); ESPN for the cross-check files | **Not** granted by the MIT license. Governed by the NBA's Terms of Use (https://www.nba.com/termsofuse) and ESPN's terms | every number in `player_game_stats` |

The MIT license on the SportsDataverse repository licenses *their* copyrightable work. It
cannot grant rights the SportsDataverse maintainers do not hold in the underlying NBA data.
Downloading a public CSV is not the same as being licensed to use its contents for any
purpose.

## NBA Terms of Use

The repository owner has read the current terms (https://www.nba.com/termsofuse) and
confirmed that they restrict the use of NBA.com statistics in connection with gambling and
certain commercial and fantasy products. The cloud environment used for development
cannot fetch the page, so the clause-level summary below is the owner's reading plus
general familiarity, and should be re-checked before any deployment decision:

* NBA.com content, which in the terms' definitions includes statistics, is made available
  for personal, non-commercial use.
* Automated collection (scraping, bots, spiders) of the site is prohibited, and the terms
  reserve the right to restrict access to stats endpoints. We do not scrape NBA.com; we
  download files SportsDataverse published. That reduces *our* technical exposure but does
  not change what the underlying data may be used for.
* Commercial exploitation of the content requires NBA permission.

Raw sports facts (who scored how many points) have historically received limited
copyright protection in the United States, but database rights, terms-of-use contracts
and the NBA's commercial data licensing programme are separate questions, and the picture
differs outside the US.

## Why this matters for this project specifically

The stated long-term goal is PrizePicks wagering analysis. Three escalating uses carry
different risk:

1. **Personal research and backtesting on a laptop** (today): lowest risk. Still relies on
   data whose terms are personal/non-commercial.
2. **Hosting a dashboard or API that others use**: becomes distribution of derived data.
   "Non-commercial" becomes hard to argue once there are users, even without payment.
3. **Anything monetised or tied to wagering**: the NBA licenses official betting data
   through authorised partners (Sportradar and Genius Sports have held such rights). Using
   unofficial feeds in a gambling product is the exact use those licences exist to govern,
   and operators in regulated markets are frequently required to use official data.

Treat **stage 2 and 3 as blocked** until one of the options below is resolved. Project
posture as of Phase 3.5: a general-purpose, non-commercial basketball forecasting and
software-research project operating under the source terms. No gambling-specific feature
is built, deployed or distributed on the restricted NBA.com-derived data. The provider
layer is modular so an appropriately licensed feed can replace it without touching the
engine.

## Options to investigate before deployment

| Option | What it would resolve | Notes |
| --- | --- | --- |
| Licensed data vendor (Sportradar, Genius Sports, SportsDataIO, Stats Perform) | authorised commercial / betting use, injuries, lineups, often odds | enterprise pricing; adapter pattern already supports swapping |
| BALLDONTLIE or similar documented API | clear API terms for *that* provider | still redistributed NBA data; read their terms for commercial and betting clauses |
| Written permission from the NBA | definitive | unlikely for a small project |
| Restrict the product to personal, non-distributed use | stays within the most defensible reading of current terms | limits the product vision |

## Provenance controls already in place

* Every stored row records its ingestion run, source name and the provider's own IDs.
* Raw downloaded files are cached locally and not committed.
* Synthetic fixtures are labelled and kept out of real-data databases.
* No credentials, scraping or access-control circumvention are used anywhere.

## Requirement for any future PrizePicks-specific integration

Before any feature that evaluates or recommends PrizePicks selections is built on real
data, the statistics feeding it must come from a source whose terms permit that use.
Candidates to evaluate, in rough order of fit: Sportradar and Genius Sports (official NBA
data partners with betting rights), Stats Perform / Opta, SportsDataIO (check the
gambling clause of the specific plan), and a direct NBA licence. The CSV and adapter
interfaces mean such a feed is a new `GameLogProvider`, not a rewrite.

## Action items

1. Re-read the live NBA Terms of Use from a normal browser and record the relevant clauses here.
2. Decide the intended deployment stage before Phase 4 (API/UI) ships to anyone else.
3. If the goal remains wagering analysis for more than one user, start vendor conversations
   early; data licensing lead time is long.

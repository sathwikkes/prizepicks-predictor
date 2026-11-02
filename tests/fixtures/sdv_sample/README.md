# SportsDataverse sample (real data excerpt)

These two files are unmodified row subsets of the real SportsDataverse release assets
for the 2024-25 NBA season (`nba_stats_player_boxscores/player_boxscores_2025.csv` and
`nba_stats_player_game_logs/player_game_logs_2025.csv`, downloaded 2026-11-02). They
cover five games: an NBA Cup group-stage regular-season game, two regular-season games for a player who changed teams
mid-season, one playoff game, and one play-in game that has no team-log row.

They exist so the adapter can be tested offline against the real schema. They are not a
dataset for evaluating model accuracy. Source repository license: MIT
(https://github.com/sportsdataverse/sportsdataverse-data); underlying statistics are
NBA.com data.

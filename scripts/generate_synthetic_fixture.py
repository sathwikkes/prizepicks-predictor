"""Generate the SYNTHETIC game-log fixture at data/samples/synthetic_game_logs.csv.

The output is deterministic (fixed seed). Player names are fictional and every
stat line is randomly generated. This file is NOT real NBA data and must never
be presented as such. It exists so the ingestion, storage and query layers can
be exercised without a live data provider.

Usage: uv run python scripts/generate_synthetic_fixture.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "samples" / "synthetic_game_logs.csv"
SEED = 20241022

# Fictional players: (id, name, team, mean_minutes, pts_rate, reb_rate, ast_rate) per minute.
PLAYERS = [
    ("FIX-001", "Alex Rivera", "BOS", 34.0, 0.78, 0.21, 0.16),
    ("FIX-002", "Jordan Nakamura", "BOS", 29.0, 0.42, 0.33, 0.08),
    ("FIX-003", "Sam Okafor", "LAL", 36.0, 0.85, 0.24, 0.25),
    ("FIX-004", "Casey Lindqvist", "LAL", 24.0, 0.35, 0.18, 0.12),
    ("FIX-005", "Morgan Adeyemi", "DEN", 33.0, 0.70, 0.30, 0.20),
    ("FIX-006", "Riley Castellano", "MIA", 31.0, 0.60, 0.15, 0.22),
]
OPPONENTS = ["BOS", "LAL", "DEN", "MIA", "NYK", "GSW", "PHX", "MIL", "DAL", "PHI"]


def main() -> None:
    rng = random.Random(SEED)
    start = date(2024, 10, 22)
    rows: list[dict[str, object]] = []
    game_counter = 1000

    for pid, name, team, mean_min, pts_r, reb_r, ast_r in PLAYERS:
        day = start
        for _ in range(12):
            day += timedelta(days=rng.choice([2, 2, 3, 3, 4]))
            opp = rng.choice([o for o in OPPONENTS if o != team])
            is_home = rng.random() < 0.5
            minutes = max(0.0, min(48.0, rng.gauss(mean_min, 4.5)))
            pts = max(0, round(rng.gauss(minutes * pts_r, 6)))
            reb = max(0, round(rng.gauss(minutes * reb_r, 2.5)))
            ast = max(0, round(rng.gauss(minutes * ast_r, 2)))
            team_score = rng.randint(95, 130)
            opp_score = rng.randint(95, 130)
            game_counter += 1
            rows.append(
                {
                    "source_game_id": f"SYN-{game_counter}",
                    "game_date": day.isoformat(),
                    "season": 2024,
                    "player_id": pid,
                    "player_name": name,
                    "team": team,
                    "opponent": opp,
                    "home_away": "home" if is_home else "away",
                    "minutes": f"{int(minutes)}:{round((minutes % 1) * 60) % 60:02d}",
                    "points": pts,
                    "rebounds": reb,
                    "assists": ast,
                    "steals": rng.randint(0, 3),
                    "blocks": rng.randint(0, 2),
                    "turnovers": rng.randint(0, 5),
                    "fg3m": rng.randint(0, 5),
                    "started": "true" if mean_min >= 29 else "false",
                    "team_score": team_score,
                    "opponent_score": opp_score,
                }
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} synthetic rows to {OUT}")


if __name__ == "__main__":
    main()

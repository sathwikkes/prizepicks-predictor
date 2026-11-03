# Project conventions for AI-assisted sessions

## Attribution

- All commits are authored and committed as the repository owner (git `user.name`
  "Sathwik Kesappragada", `user.email` sathwik.kesappragada97@gmail.com). Never commit as
  "Claude" or any other identity.
- Do not add `Co-Authored-By`, `Claude-Session`, or any other AI attribution trailers to
  commit messages.
- Do not add "Generated with Claude Code" footers or session links to pull request
  descriptions, issues, or comments.
- Do not name branches after the assistant. Use `feature/<topic>` names.

## Engineering

- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` and
  `uv run mypy` before every commit.
- Keep synthetic fixtures labelled as synthetic and out of real-data databases.
- Never claim model accuracy, calibration or profitability beyond what
  `docs/BACKTEST_RESULTS.md` supports.

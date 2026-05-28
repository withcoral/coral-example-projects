# Contributing

Thanks for your interest. This repo is a collection of working example
projects that show how to build production-style agents on top of Coral.
Bug reports, fixes, and improvements that keep the examples honest and
runnable are very welcome.

## Reporting bugs and requesting features

Open a [GitHub Issue](../../issues). For bugs, include:

- What you ran (commands, environment)
- What you expected
- What actually happened (full output / stack trace if possible)
- Versions of relevant tools (`python --version`, `coral --version`, OS)

For features, describe the use case before the proposed solution — the most
useful issues are framed as "I'm trying to do X and ran into Y" rather than
"please add Z".

## Pull requests

1. Fork the repo and create a topic branch off the default branch.
2. Make your change. Keep PRs scoped — one logical change per PR.
3. Run the tests and linter for the project you touched:
   ```bash
   cd SRE-agent
   uv sync --extra dev
   uv run pytest
   uv run ruff check .
   ```
4. Open the PR with a clear description of *what* changed and *why*. Link
   any related issue.

## Style

- Prefer small, reviewable PRs.
- Match the existing code style — `ruff` is the source of truth.
- Update the relevant README / GUIDE / docs when behavior changes.
- Don't commit secrets. `.env` is gitignored; if you add a new env var,
  add it to `.env.example` with a brief comment.

## License

By submitting a contribution you agree that it will be released under the
[Apache 2.0 License](LICENSE) that covers this repository.

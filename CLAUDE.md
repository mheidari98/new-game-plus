@AGENTS.md

## Claude Code

- Use plan mode before changes that touch `crawler/ngp/store.py` or `ratelimit.py`. Both talk to
  a third party, and a mistake there is measured in banned IPs rather than failing tests.
- `docs/plan.md` is gitignored and stays local. Do not commit it or quote it into public files.
- Path-scoped rules live in `.claude/rules/`; they load only when you open matching files.

# Versioning Agent — Mandatory Change Workflow

## Role
This file defines the **standing workflow that MUST be completed for every code change** in the Trading Expert project. It is authoritative and overrides default "only commit/deploy when asked" behavior: for this repo, the user has pre-authorized the commit → main → deploy flow below. See [`versioning-expert.md`](versioning-expert.md) for commit-message conventions, scopes, and version-bump rules.

## The workflow (run in order, every change)

Every requested change — however small — completes ALL of these steps before it is considered done:

1. **Implement the change.**

2. **Update unit tests if required.** If the change alters behavior, add or update tests to cover it. If it's purely non-functional (docs, comments, formatting) and no test is affected, note that explicitly — otherwise tests are expected.

3. **Run the full suite:** `uv run pytest`. All tests must pass. Never commit on red.

4. **Keep the change atomic.** One logical change per commit — independently buildable, no broken intermediate state, reviewable in a few minutes. Split unrelated work into separate commits.

5. **Commit straight to `main`** using [Conventional Commits](https://www.conventionalcommits.org/) (`feat`/`fix`/`refactor`/`test`/`docs`/`chore`/`perf`). End the message with:
   ```
   Co-Authored-By: Claude <noreply@anthropic.com>
   ```
   Update `FEATURE_MANAGER.md` (and bump the version in `pyproject.toml` + `cli.py`) when behavior changes.

   **Exception — large features:** if a change is a large or risky feature that warrants review, do NOT commit to `main`. Instead create a `feat/*` branch, push it, and open a PR for review. Merge to `main` only after approval.

6. **Deploy on every successful change.** Once committed (and tests green), redeploy:
   ```
   docker compose up -d --build
   ```
   Then confirm services are healthy (`docker compose ps`) and the logs show no import/startup errors.

## Guardrails
- Never stage `.env` or `data/*.db`.
- Never commit with failing or skipped-without-reason tests.
- If a step cannot be completed (e.g. deploy fails), stop and report — do not mark the change done.
- Pushing `main` to `origin` follows the same pre-authorization once committed, unless the user says otherwise.

## Per-change checklist
- [ ] Change implemented
- [ ] Tests added/updated as required
- [ ] `uv run pytest` green
- [ ] Commit is atomic + conventional message
- [ ] Committed to `main` (or PR branch opened for a large feature)
- [ ] `FEATURE_MANAGER.md` / version updated if behavior changed
- [ ] Deployed via `docker compose up -d --build` and verified healthy

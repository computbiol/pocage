# Git Commit Message Convention

This project follows the Conventional Commits specification.

Basic format:

```text
<type>[optional scope][!]: <description>
```

Examples:

```text
feat(frontend): add session resume button
fix(backend): handle missing session id
docs: clarify release tag policy
ci(pypi): publish package only on pocage-v tags
chore(release): 0.3.1
```

## Type

Use the standard Conventional Commits types:

| Type | Meaning |
|---|---|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation-only changes |
| `style` | Formatting or style-only changes that do not affect behavior |
| `refactor` | Code changes that neither fix a bug nor add a feature |
| `perf` | Performance improvements |
| `test` | Adding or updating tests |
| `build` | Changes affecting build tools, packaging, or dependencies |
| `ci` | Changes to CI/CD workflows |
| `chore` | Repository maintenance or housekeeping |
| `revert` | Reverts a previous commit |

Do not introduce a custom `release` type.

## Scope

The `scope` is optional.

When used, prefer a short noun that identifies the affected area, such as:

```text
backend
frontend
connector
infra
repo
api
cli
auth
session
pypi
release
```

Choose the clearest scope for the actual change. Do not add a scope if it does not add useful information.

## Description

The `description` is the short subject line after the colon.

Rules:

- Use English.
- Use lowercase unless a proper noun is required.
- Use the imperative mood.
- Do not end with a period.
- Keep it concise, preferably under 72 characters.

Good examples:

```text
feat(connector): add daemon reconnect backoff
fix(frontend): handle empty release notes
docs: clarify PyPI publishing rules
ci(pypi): verify tag matches package version
```

Bad examples:

```text
feat(connector): added daemon reconnect backoff.
fix(frontend): Fixed a bug.
docs: update
chore: misc changes
```

## Breaking Changes

For a breaking change, add `!` before the colon and explain the change in a footer.

Example:

```text
feat(api)!: change session response schema

BREAKING CHANGE: the session response now returns `session_id` instead of `id`.
```

## Release Commits

Git tags and GitHub releases are separate from commit message format.

Creating a tag does not require a dedicated release commit.

If a commit is specifically for release preparation, use `chore(release)`:

```text
chore(release): 0.3.1
chore(release): pocage-v0.1.2
```

## Recommended Practice

Each commit should represent one logical change.

Prefer:

```text
fix(api): validate session id before lookup
docs(release): clarify version tag policy
chore(repo): clean up local development scripts
```

Avoid:

```text
fix: update api, docs, frontend, and release config
```

## Historical Commits

Existing historical commit messages do not need to be rewritten.

Apply this convention to new commits going forward.

# Contributing to Stitch

## Development

```bash
bun install
bun run build
bun run typecheck
bun test
```

## Release Workflow

Stitch uses an automated release pipeline. No manual `git tag` or `npm publish` is needed.

### Steps

1. **Branch from `main`** as `release/v.X.Y.Z` (note the dot after `v`):
   ```bash
   git checkout main && git pull
   git checkout -b release/v.2.1.0
   ```

2. **Bump the version** in `package.json`:
   ```bash
   bun version 2.1.0 --no-git-tag-version
   ```

3. **Open a Merge Request** targeting `main`. CI runs lint, knip, typecheck, and tests.

4. **Merge the MR.** On merge, CI detects the `release/v.X.Y.Z` branch name in the commit message and automatically:
   - Creates a `vX.Y.Z` git tag (within ~60s)
   - Publishes the package to npm (within ~3 min after tagging)
   - Syncs the tag to the GitHub mirror

### CI Variables Required

| Variable | Scope | Purpose |
|---|---|---|
| `GITLAB_PUSH_TOKEN` | `api`, `write_repository` on `main` + tags | Used by `tag:release` to push the git tag |
| `NPM_TOKEN` | protected | Used by `publish:npm` to publish to the npm registry |
| `GITHUB_TOKEN` | protected | Used by `sync:github` to mirror to GitHub |
| `GITHUB_REPO` | protected | Target GitHub repo (e.g. `x24labs/stitch`) |

### Version format

Branch names follow `release/v.X.Y.Z` (with a dot after `v`). The CI extracts this and creates tag `vX.Y.Z` (no dot).

## Commit Conventions

- Format: `type(scope): description`
- Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`
- Reference issues with `#N` when applicable

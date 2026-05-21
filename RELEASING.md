# Pocage Versioning and Release Convention

Pocage is a monorepo that contains the `connector/` Python package, backend service, frontend app, deployment configuration, and documentation.

This project uses separate version tags for the whole project and for the PyPI package.

These two version lines evolve independently.

- The whole-project version tracks product-level releases.
- The `connector/` package version tracks PyPI releases.
- They do not need to match.
- If the same milestone affects both, matching numbers are allowed but optional.

## 1. Project Version

Use `v<version>` for the whole Pocage project.

Examples:

```text
v0.3.0
v0.3.1
```

Meaning:

```text
v0.3.0 = whole-project release
```

Use this tag for GitHub Releases and project-level version tracking.

This tag may include changes in:

```text
frontend/
backend/
connector/
deployment files
documentation
overall product behavior
```

The `v<version>` tag must not trigger PyPI publishing.

## 2. PyPI Package Version

Use `pocage-v<version>` for the Python package under `connector/`.

Examples:

```text
pocage-v0.1.2
pocage-v0.1.3
```

Meaning:

```text
pocage-v0.1.2 = publish pocage==0.1.2 to PyPI
```

The version in `connector/pyproject.toml` must match the tag.

Example:

```toml
[project]
version = "0.1.2"
```

Then the corresponding PyPI tag must be:

```text
pocage-v0.1.2
```

Only create a `pocage-v<version>` tag when the `connector/` package has a real update and needs to be published to PyPI.

## 3. Version Relationship

The whole-project version and the package version are independently versioned.

This means:

```text
v0.3.0       and pocage-v0.1.2 can both be valid at the same time
v0.3.1       does not require pocage-v0.3.1
pocage-v0.1.3 does not require v0.1.3
```

Rules:

- Do not bump the package version just because frontend, backend, infra, or docs changed.
- Do not create a project release tag just because the Python package changed.
- If one milestone intentionally releases both the product and the package, matching version numbers are allowed but not required.

## 4. GitHub Actions Rule

The PyPI publishing workflow should only listen to `pocage-v*` tags.

```yaml
on:
  push:
    tags:
      - "pocage-v*"
```

The version check should use:

```bash
expected_tag="pocage-v${package_version}"
```

This ensures that:

```text
pocage-v0.1.2 -> triggers PyPI publishing
v0.1.2        -> does not trigger PyPI publishing
```

## 5. Publishing a PyPI Package

When releasing a new `connector/` package version:

```bash
# 1. Update connector/pyproject.toml
# version = "0.1.2"

git add connector/pyproject.toml
git commit -m "Bump pocage package to 0.1.2"

git tag pocage-v0.1.2
git push origin main
git push origin pocage-v0.1.2
```

If this package release also participates in a whole-project release, create a separate project tag:

```bash
git tag v0.3.0
git push origin v0.3.0
```

The two tags may point to the same commit, but they do not need to use the same version number.

## 6. Publishing a Project Release

If only the frontend, backend, deployment configuration, or documentation changes:

```bash
git add .
git commit -m "Update frontend and backend"
git push origin main
```

If a whole-project release tag is needed:

```bash
git tag v0.3.1
git push origin v0.3.1
```

Then create a GitHub Release from `v0.3.1`.

## 7. Historical Tags

Existing historical tags should not be deleted or rewritten.

From the next release onward, use:

```text
v<version>        = whole-project version / GitHub Release
pocage-v<version> = connector Python package version / PyPI Release
```

## 8. Final Rule

```text
v*        = whole Pocage project release
pocage-v* = connector/ Python package release to PyPI
```

In one sentence:

`v*` is for the whole project; `pocage-v*` is for the PyPI package; the two version numbers evolve independently.

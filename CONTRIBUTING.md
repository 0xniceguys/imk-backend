# Contributing

Thanks for contributing to Immortal Kombat.

## Before You Start

- Read the project overview in `README.md`.
- Keep pull requests focused. Small, reviewable changes move faster.
- For large feature work or architecture changes, open an issue first so scope and direction are clear before implementation starts.

## Development Setup

### Backend

1. Copy the environment shape from `backend/.env.example`.
2. Install Python dependencies for the backend you are working on.
3. Run the API locally and verify the affected endpoints before opening a PR.

### Flutter app

1. Use a recent stable Flutter toolchain.
2. Work from `streaming/flutter_app`.
3. Validate user-facing changes with a local `flutter build apk --release` when your change affects release behaviour, packaging, or Android-specific code.

### Training and emulator stack

1. Review the local tooling instructions in `README.md`.
2. Keep ROMs, secrets, and local runtime data out of commits.
3. Document any new external runtime dependency you introduce.

## Pull Request Expectations

- Base your branch on `main`.
- Include a clear summary of what changed and why.
- Add or update docs when behaviour, setup, or release flow changes.
- Include verification notes:
  - commands run
  - tests run
  - builds run
  - known gaps, if any
- Do not commit secrets, API keys, wallet keys, ROMs, or machine-specific config.

## Code Style

- Match the existing style of the area you touch.
- Prefer explicit, boring solutions over clever ones.
- Keep comments short and only where they remove ambiguity.
- Avoid unrelated refactors in the same PR.

## Release Changes

If your PR affects release packaging, GitHub Actions, or public install instructions:

- update the relevant workflow or docs
- verify the release artifact path still matches the workflow
- call out any versioning or tagging impact in the PR description

## Reporting Problems

- Use the issue templates for bugs and feature requests.
- For security-sensitive findings, follow `SECURITY.md` instead of opening a public issue.

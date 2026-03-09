# Immortal Kombat Flutter App

Flutter app source lives in `streaming/flutter_app`.

## Android releases

This repo now supports two GitHub Actions release paths through `.github/workflows/flutter-release.yml`.

### 1. Tag-driven release

Use this when the Flutter version in `pubspec.yaml` is already updated on `main`.

1. Update `version:` in `streaming/flutter_app/pubspec.yaml` to the target release, for example `1.2.3+7`.
2. Commit and push that change to `main`.
3. Create and push a matching git tag such as `v1.2.3`.
4. The workflow builds a single Android release APK and publishes a GitHub release for that tag.

Rules:
- The tag must match the semantic version in `pubspec.yaml`.
- The tag commit must already be contained in `main`.

### 2. Manual release from GitHub Actions

Use this when you want Actions to handle the version bump, commit, tag, build, and release in one run.

1. Open the `Flutter Android Release` workflow in GitHub Actions.
2. Run it against the `main` branch.
3. Enter a semantic version such as `1.2.3`.
4. Optionally enter a numeric build number. If left blank, the workflow increments the current build number.
5. The workflow updates `streaming/flutter_app/pubspec.yaml`, pushes the version bump to `main`, creates tag `v1.2.3`, builds the APK, and publishes the GitHub release.

## What this workflow builds

- `app-release.apk`

## Current limitation

This automation is Android-only right now. The repo does not contain a complete iOS project and signing setup, so iOS release builds are not ready for GitHub Actions yet.

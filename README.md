# Open 3D Atlas

Open 3D Atlas is an open-source, offline-first scientific 3D atlas for web, Android, and iOS. Its goal is to let users explore downloaded 3D models and their scientific annotations without a network connection. This repository foundation defines where each part of the platform will live.

## Architecture

| Directory | Responsibility |
| --- | --- |
| [backend/](backend/) | Python 3.12+ and FastAPI REST service. Owns server-side domain logic and PostgreSQL 16 persistence. |
| [processing/](processing/) | Python and Blender workflows for validating and preparing scientific 3D assets. |
| [android/](android/) | Kotlin and Jetpack Compose client. |
| [ios/](ios/) | Swift and SwiftUI client. |
| [web/](web/) | React and TypeScript client. |
| [contracts/](contracts/) | Versioned OpenAPI descriptions for the REST interface and shared data formats. |
| [docs/](docs/) | Project documentation, including asset licensing. |
| [deployment/](deployment/) | Deployment documentation and reusable infrastructure templates. |

Clients use the REST API to retrieve updates and read downloaded atlas content from local storage. The backend owns database access. Scientific processing produces versioned assets and metadata outside the API request and client rendering paths.

## Development workflow

1. Update your local `develop` branch and branch from it with `git switch -c feature/<topic>`.
2. Follow the [repository guidance](AGENTS.md) and work within the directories relevant to your change. Update the OpenAPI contract alongside any API behavior change.
3. Run the affected components' documented checks as they become available. For this foundation, run `git diff --check` and `git diff --cached --check`, and review the changed files for valid links and accidental secrets.
4. Open a pull request targeting `develop`. Describe what changed, the validation you ran, and any impact on compatibility, offline use, or asset licensing.

## Contribution principles

Keep changes focused and reviewable. Preserve backward compatibility where possible and test offline behavior for client changes. Keep scientific provenance and attribution with each contributed asset, and use small synthetic or redistribution-cleared fixtures for tests.

## Licensing

Repository source code is licensed under the [MIT License](LICENSE). Scientific content and media have asset-specific terms documented in the [asset licensing policy](docs/ASSET-LICENSING.md).

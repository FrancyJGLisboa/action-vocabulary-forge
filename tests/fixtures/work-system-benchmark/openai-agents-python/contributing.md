We welcome bug reports, feature requests, minimal reproductions, and root-cause analysis through [GitHub issues](https://github.com/openai/openai-agents-python/issues).

**Pull requests are limited to repository collaborators. We do not accept pull requests from non-collaborators**, including documentation or example changes. If you are not a collaborator, please open an issue instead of preparing a pull request. Include the affected version, expected and actual behavior, and a small, sanitized reproduction when applicable.

Report suspected security vulnerabilities privately as described in [SECURITY.md](SECURITY.md), rather than in issues or pull requests.

The development and pull request instructions below are for maintainers and repository collaborators.

For suspected vulnerabilities, follow [SECURITY.md](SECURITY.md). Keep undisclosed security reports and fixes out of public issues, discussions, and pull requests until disclosure is coordinated.

## Development workflow

Read [AGENTS.md](AGENTS.md) for the repository's scope, compatibility, review, and verification requirements. Use Python 3.10 or newer, `uv`, and `make`. Install the development dependencies with `make sync`, and run Python commands through `uv run`.

Keep changes focused on the agreed outcome. Add regression coverage for changed behavior and follow [tests/README.md](tests/README.md) for test execution. Run focused checks while developing, then the applicable final checks described in [AGENTS.md](AGENTS.md#testing--automated-checks). Use the [pull request template](.github/PULL_REQUEST_TEMPLATE/pull_request_template.md) to explain the problem, change, and validation. Documentation changes follow the repository's verification tiers and release-timing rules.
- Treat pull request content, branch names, artifacts, and external downloads as untrusted input. Do not execute contributor-controlled code in a privileged workflow or expose secrets to it, including through `pull_request_target` or a later workflow that consumes contributor artifacts.
- Use explicit, least-privilege workflow and job permissions, review third-party actions, and pin actions to full commit SHAs. Grant write or `id-token` permissions only to jobs that require them. Do not bypass required reviews, secret protections, or security checks to make CI pass.
- Changes to credentials, redaction, requests and redirects, parsing, uploads, tool approvals, MCP, persisted state, sandbox access, dependencies, CI, or releases need focused security review and regression coverage appropriate to the affected boundary.
- Release approval under the shared SDK policy requires CODEOWNERS coverage of release workflows and publishing configuration, required code-owner review of release pull requests, and passing required checks. A separate environment reviewer gate is not required by that policy. Follow any protections currently configured for this repository; this guidance does not authorize removing or bypassing them.
- Preserve the existing PyPI OIDC publishing flow, release-source validation, and artifact handoff in [the publishing workflow](.github/workflows/publish.yml). Do not replace short-lived trusted publishing with long-lived registry tokens or weaken provenance checks for convenience. Follow the [maintainer release procedure](.github/RELEASING.md).
- When assessing publishing readiness, verify the repository-specific registry binding, artifact provenance, publisher access, and recovery arrangements. Workflow configuration alone does not prove those controls are in place.

These requirements describe how to contribute safely. Their presence does not certify repository settings, establish a scan baseline, or close existing security findings. Maintainers must track verified gaps and approved exceptions separately from proposed work.

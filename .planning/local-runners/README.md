# Enable local runners for Suprnova LSP

## Scope and existing contracts

The closed responsiveness commitment explicitly leaves CI policy, packaging and
releases to separate work (`docs/spec/roadmap.md`, indexing-responsiveness).
The new CIR requirements preserve IDN-004's artifact identities and attribution;
no existing Agreed requirement, working-agreement text or Sudus setting changes.

## Implementation

1. Use the existing runner installers to create separate Suprnova LSP registrations
   on Linux x64, Windows x64 and macOS ARM64. Preserve pilot services and capture
   owner default Rust toolchains outside repository overrides before and after.
2. Route trusted native jobs in `ci.yml`, `build-server.yml` and
   `platform-checks.yml`, plus matching `github-release.yml` package jobs, to exact
   OS/architecture labels. Keep reusable workflow artifact identifiers independent
   of runner labels. Check both initiating and rerunning actors; keep PRs hosted.
3. Install/select explicit Rust versions on local job paths and respect the
   configured Cargo home in `.github/actions/cargo-cache/action.yml`. Retain the
   Linux manylinux release container. Other hosted-only jobs remain hosted.
   Windows jobs add Git Bash to their job PATH. Mac and Windows select portable
   Python under their consumer runner's `tools/` directory through the service
   variable `SUPRNOVA_LSP_CI_PYTHON`; `.github/runner-tools.json` pins the archives
   and hashes. This avoids hosted-user paths and machine-wide Python installers.
4. Enable Actions and require approval for all external fork contributors. Disable
   the legacy `release.yml` marketplace automation before enabling owner pushes.
   Keep GitHub tag releases available without marketplace credentials.
5. Add a manually dispatched three-platform smoke workflow. Verify actual job
   identities, explicit-toolchain compilation/tests and downloaded artifacts.

## Verification and delivery

Add focused routing and evidence tests with violating controls; validate Actions
syntax. Record real settings, runner services, preserved pilot registrations and
owner toolchains, job IDs and artifacts. Attempt normal CI without disguising any
inherited portability failure as a runner success. Commit and push the configured
workflows through Sudus; complete builder and fresh adversarial review.

Release publication, framework checkout changes, setup-repository source edits,
new host resource policies and unrelated application fixes are excluded.

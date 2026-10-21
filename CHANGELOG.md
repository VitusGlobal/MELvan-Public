# Changelog

This changelog begins with the first reviewed public-release candidate. Private
development chronology and acceptance records are intentionally excluded.

## 1.2.5 - 2026-10-21

- Added complete Reviewer controls for direction, category, and change types
  when correcting a non-Outcome Statement to an Outcome.
- Made the complete correction authoritative through atomic Evidence
  acceptance and retained every Outcome attribute as future learning evidence.
- Replaced fabricated Outcome defaults and unreadable failures with specific
  validation for missing or category-incompatible metadata.
- Added service, API, interface-contract, learning, SQLite, and PostgreSQL
  migration coverage for the corrected workflow.

## 1.2.4 - 2026-10-20

- Prevented harmless Outcome-only metadata returned with an Activity, Output,
  or Indicator classification from causing Statement analysis to fail.
- Preserved strict Outcome validation while explicitly constraining non-Outcome
  responses and advancing the audited Statement-analysis prompt contract.
- Added regression coverage for the contradictory provider response.

## 1.2.3 - 2026-10-12

- Corrected migration connection selection so Alembic uses the explicitly
  supplied migration identity instead of an application runtime URL present in
  the environment.
- Kept the pinned Azurite integration service compatible with newer Azure
  Storage SDK API headers by enabling the emulator's API-version bypass.
- Added contract coverage for both integration-job requirements.

## 1.2.2 - 2026-10-12

- Allowed intentional copyright-holder attribution in `LICENSE.md` without
  weakening publication scans across application, deployment, and product
  documentation files.
- Added regression coverage for license and notice attribution exclusions in
  both the `ripgrep` and standard `grep` publication-check paths.

## 1.2.1 - 2026-10-12

- Made the publication gate portable to GitHub-hosted runners that do not
  include `ripgrep`, while retaining `ripgrep` as the faster local path when
  available.
- Clarified that default CI is repository-local and requires no Azure account;
  Azure deployment remains a separate manual-only workflow.

## 1.2.0 - 2026-09-17

- Added a shared, read-only **About MELvan** tab for Owner, Administrator,
  Reporter, and Reviewer sessions.
- Added the complete MELvan FAQ, including attribution, methodology, quality
  measures, suitability guidance, access options, and open resources.
- Kept the feature informational and made the expanded navigation usable on
  narrow screens without changing role permissions or program workflows.

## 1.1.1 - 2026-09-15

- Clarified that Administrator metrics count scoring events, including separate
  Logic Model update reviews of existing Evidence.
- Broke the total into initial Statement reviews and Logic Model update reviews,
  and displayed the current Evidence-record count for reconciliation with PDF
  exports.
- Preserved lifetime-score calculations and one-row-per-current-Evidence export
  behavior.
- Replaced the AGPL software license with the MIT License and added project
  attribution and machine-readable citation metadata.

## 1.1.0 - 2026-09-15

- Added an Administrator-only **MELvan metrics** tab beside **Users**.
- Displayed every established workspace lifetime measure using the Reviewer
  report's numerator, eligible denominator, and percentage calculation.
- Added a clear no-scored-reviews state and enforced active-role and workspace
  isolation at the API boundary.

## 1.0.1 - 2026-09-15

- Prevented model-generated `WP-*` working Outcomes from being created or shown
  as Logic Model mappings.
- Limited displayed and selectable mappings to nodes in committed Logic Model
  versions; otherwise MELvan records **No suitable existing match**.
- Preserved historical synthetic rows for audit while excluding them from
  display, future matching, and Evidence acceptance.
- Constrained the model prompt and response schema so providers do not spend
  tokens drafting framework elements that only Administrators may create.

## 1.0.0 - 2026-08-19

- Marked MELvan's first stable product milestone following acceptance of the
  first-time registration journey and required User Guide acknowledgement.
- Established the current role-aware reporting, review, evidence, logic-model,
  learning, media-capture, administration, and owner capabilities as the 1.0
  functional baseline.

## 0.11.6 - 2026-08-18

- Completed the first-time password setup journey with a required User Guide
  acknowledgement immediately above the submit action.
- Moved the complete User Guide into an accessible linked dialog while keeping
  password recovery free of the first-time acknowledgement requirement.

## 0.11.5 - 2026-08-18

- Restored the canonical MELvan product principles and full health-domain
  safeguards to the public distribution.
- Established the Global Affairs Canada and W.K. Kellogg Foundation references
  as canonical sources of MELvan's internal logic, with official links retained
  when source binaries cannot be redistributed.
- Restored the PMF as a program-level source of truth alongside the Logic Model.
- Made RECAP a fixed MELvan concept and updated its canonical link instead of
  allowing forks to replace it through deployment configuration.
- Added publication checks that prevent these product foundations from being
  omitted in future public candidates.
- Clarified that persistence profiles are mutually exclusive and that the
  production profile uses PostgreSQL without SQLite.
- Added an opt-in Azure production deployment baseline with PostgreSQL Flexible
  Server, private Blob attachments, a fail-closed migration job, restricted
  runtime database credentials, and multiple stateless web replicas.

## 0.11.4 - 2026-08-18

- Prepared a history-free, operator-neutral public-release candidate.
- Added current-state product, architecture, configuration, operations,
  security, deployment, and testing documentation.
- Replaced development-phase policy names with semantic identifiers.
- Made Azure deployment identity and resource settings fork-owned.
- Added a server-enforced, versioned MELVan User Guide acknowledgement during
  initial password creation while keeping later password recovery unchanged.

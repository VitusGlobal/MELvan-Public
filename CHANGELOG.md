# Changelog

This changelog begins with the first reviewed public-release candidate. Private
development chronology and acceptance records are intentionally excluded.

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

# Changelog

## 1.5.0 - 2026-11-26

- Replaced temporary passwords with purpose-specific, single-use setup and reset links.
- Expired first-time setup links after seven days and reset links after 24 hours while storing only token hashes.
- Revoked prior links, passwords, temporary lockouts, and active sessions when an authorized account manager resets a password.
- Sent users directly to the restricted create-password screen while preserving first-time User Guide acknowledgement.
- Offered only pre-addressed Gmail, Outlook, and default-mail-client drafts; removed password and link copy actions.
- Added a replayable PostgreSQL migration and equivalent operator-only Owner setup link.

## 1.4.0 - 2026-11-25

- Move temporary-password creation and reset into authorized Owner and Administrator user-management workflows.
- Display each 12-character, seven-day temporary password once with copy, Gmail, Outlook, and default-mail-app sharing actions.
- Replace rather than recover passwords; reset revokes prior credentials and sessions immediately.
- Remove the public password request endpoint and Azure Communication Services Email from the maintained runtime and deployment topology.
- Audit credential issuance and sharing-channel actions without retaining plaintext.

## 1.3.1 - 2026-11-25

- Verify independently that first-time password setup and password reset both invoke email delivery, with distinct internal purposes and the same account-enumeration-safe public response.
- Verify that Azure Communication Services receives the exact MailFrom address configured through `MELVAN_EMAIL_SENDER`.
- Document the custom-domain requirement for a branded sender; each deployment configures its own verified MailFrom address and display name.

This changelog begins with the first reviewed public-release candidate. Private
development chronology and acceptance records are intentionally excluded.

## 1.3.0 - 2026-11-25

- Added an accessible, collapsed workspace accordion beneath each Administrator
  on the Owner's **Administrators** page.
- Loaded one validated workspace's Reporter, Reviewer, and dual-role users only
  when the Owner expands it.
- Displayed roles, login activity, applicable Statement and review totals,
  temporary lockout, individual status, and inherited workspace suspension.
- Kept nested users read-only while preserving the existing parent
  Administrator edit, suspension, and reactivation controls.
- Added workspace-isolation, authorization, interface-contract, and rendered
  browser coverage for the Owner overview.

## 1.2.9 - 2026-11-01

- Displayed no more than the two highest-confidence Logic Model mappings,
  ordered from highest to lowest confidence, without removing stored mappings.
- Placed the classification rationale immediately after the first mapping or
  the no-match message.
- Applied the same two-mapping presentation rule to Logic Model update Evidence
  review and improved confidence-label wrapping on narrow screens.
- Added contract and rendered-browser coverage for ordering, rationale
  placement, the display limit, and mobile overflow.
- Documented the stored-versus-displayed mapping boundary, sub-25% fallback
  exception, tense-sensitive result interpretation, bounded repair, and
  diagnostic behavior in the stable public product guidance.

## 1.2.8 - 2026-11-01

- Treated Reporter submissions as presumptively relevant and interpreted
  completed decisions, commitments, and new collaborations as possible results
  even when written in present or future tense.
- Reconciled unambiguous Outcome category/change-type mismatches instead of
  failing otherwise valid analyses.
- Restricted generated mappings to active Logic Model elements and added one
  bounded corrective attempt for internally inconsistent model output.
- Retained the strongest plausible current mappings when none reaches 25%,
  labelled them **Very low confidence**, and kept their rejection neutral in
  MELvan performance scoring.
- Added structured analysis diagnostics and regression coverage for the
  reported Outcome-metadata and existing-mapping failure modes.

## 1.2.7 - 2026-10-31

- Added Canadian and Indigenous Data Sovereignty to the values presented in
  **About MELvan**.
- Moved the open-access explanation into a standalone question while retaining
  the operator-responsibility and demo-environment warnings.
- Clarified the demo Logic Model choices and warned against placing
  confidential or sensitive information in the demo model.

## 1.2.6.1 - 2026-10-31

- Restored a replayable PostgreSQL migration history by keeping Reviewer
  Outcome metadata out of the historical `0001` schema and introducing it only
  through migration `0004`.
- Added regression coverage that prevents columns owned by later migrations
  from being copied back into the initial schema.
- Verified the deterministic suite and offline PostgreSQL migration chain; the
  repository integration job remains the live PostgreSQL execution check.

## 1.2.6 - 2026-10-31

- Added Open Access to MELvan's stated values and explained why open access is
  important to participation, transparency, scrutiny, and reuse.
- Clarified that the repository is intended for installation in an operator's
  own cloud environment, while the shared example-data demo is not a secure
  production environment.
- Made users and administrators explicitly responsible for security,
  confidentiality, and compliance in their own deployments.
- Removed the commercial consulting and subscription paragraph from About
  MELvan.

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

# Product guide

## Core concepts

A **Reporter** submits an immutable Statement using text, voice transcription,
or text extracted from a photo. Optional Context is stored separately and may
be revised by the Reporter until the Statement becomes Evidence.

MELvan summarizes the Statement, classifies it as an Activity, Output, Outcome,
or Indicator, and proposes zero or more mappings to the current Logic Model and
Performance Measurement Framework (PMF).
Each mapping has its own confidence and rationale. Outcome results may be
Positive or Negative; Outputs are neutral.

A **Reviewer** can accept a correctly mapped Statement, change its
classification or mappings and accept it, or mark it not relevant to the
program. A Statement must have at least one approved program-framework mapping before
it can become Evidence.

An **Administrator** manages one isolated program workspace, its Reporter and
Reviewer accounts, and its versioned Logic Model. An **Owner** creates and
manages Administrators and may observe their workspaces in read-only mode.

## First-time access

An authorized user requests a temporary password from the sign-in screen. When
creating a permanent password for the first time, the user reads and
acknowledges the current MELVan User Guide before entering the application. A
later password recovery requires password replacement but does not repeat the
onboarding acknowledgement.

## Capture

- **Text:** the Reporter enters Statement text directly.
- **Voice:** MELvan transcribes a voice note of up to 60 seconds. The Reporter
  may edit, replace, or submit the transcription. Audio is never retained.
- **Photo:** the Reporter takes or selects a photo. MELvan extracts text and
  the Reporter may edit it before submission. If no text is found, the Reporter
  explains why the photo matters. The accepted normalized photo is retained as
  a Statement attachment.

Context is optional in every mode. It may help the Reporter remember relevant
circumstances and is secondary to the Statement during analysis.

## Logic Models

A Logic Model contains:

- Five optional [RECAP](https://www.vitushq.com/en/blog/tired-of-vague-advice-on-writing-a-theory-of-change-narrative-try-the-recap-framework/) fields: Risks, Evidence, Context, Assumptions, and People.
- One or more Outputs.
- Short-, Medium-, and Long-term Outcomes.
- Exactly one Ultimate Goal.

Administrators edit an autosaved draft and explicitly commit it to create a new
immutable version. Earlier versions can be selected and restored without
erasing history. Changes to a title or RECAP field do not affect mappings.

When classification-relevant elements change, pending Statements remain in the
ordinary review queue. Previously accepted Evidence enters reassessment only
when its classification changes, a mapping changes, or mapping confidence moves
by at least 25 percentage points. Rationale-only changes do not trigger review.

The Logic Model and PMF are MELvan's two program-level sources of truth. PMF
administration and ingestion are not yet implemented, but forks must not redefine
the Logic Model as MELvan's only conceptual program framework.

## Review and Evidence

Review queues are newest first and may be filtered by classification or Logic
Model element. Supported mappings are at least 50% confident. Mappings from 25%
inclusive to below 50% are visibly tentative; mappings below 25% are suppressed.
Rejecting only a tentative mapping does not count against MELvan's quality score.

Reviewer decisions are authoritative. Corrections create an updated analysis
and can immediately promote the Statement to Evidence. Not-relevant records are
retained for audit but removed from user-facing lists and performance scoring.

Review completion records first-review approval, correct level, correct Outcome
direction where applicable, correct mapping, and absence of hallucinated or
toxic language. Session snapshots are immutable so performance trends can be
compared over time.

## Learning

Accepted and corrected review decisions become bounded, versioned precedents.
MELvan preserves its original proposal separately from Reviewer-confirmed truth.
Learning prioritizes missing mappings and surprising high-confidence errors over
routine confirmation. It does not fine-tune model weights or allow raw comments
to rewrite global instructions.

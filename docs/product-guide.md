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
Reviewer accounts, its versioned Logic Model, and a read-only **MELvan metrics**
view of all lifetime performance measures for that workspace. An **Owner**
creates and manages Administrators and may observe their workspaces in read-only
mode.

The metrics total counts scoring events, split between initial Statement reviews
and Logic Model update reviews, and is shown alongside the current Evidence
count. One Evidence record may have an initial score and a later reassessment
score, while appearing only once in the Evidence PDF export.

Every authenticated role can open the shared, read-only **About MELvan** tab.
It contains the product FAQ, project attribution, methodology, quality measures,
suitability guidance, access options, and links to open resources. This shared
information does not expand the active role's operational permissions.

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

Newly created administrator accounts can configure the logic model for their workspace - they have the choice to either clone the 'demo' model, a generic logic model for an imaginary mental health program for adults, or they can create their own logic model which must comply with the defined structure above.

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

Every completed review produces a **How MELvan did** report. It records the
number of Statements reviewed, accepted, sent back with changes, and marked Not
relevant. It then reports a numerator, denominator, and percentage for both the
completed review session and MELvan's lifetime performance in that workspace.
Completed session snapshots are immutable, allowing performance to be compared
chronologically as MELvan learns. Records marked Not relevant are retained for
audit but excluded from the performance metrics.

MELvan is assessed against five quality measures:

- **First-review approval:** whether the Reviewer's first action accepts the
  Statement and MELvan's associated decisions without a substantive correction.
  This is the primary outcome MELvan is intended to improve.
- **Correct level:** whether MELvan correctly distinguishes Activity, Output,
  Outcome, and Indicator.
- **Correct Outcome direction:** whether an Outcome is correctly identified as
  Positive or Negative. This measure is not applicable to Activities, Outputs,
  or Indicators and they are excluded from its denominator.
- **Correct mapping:** whether MELvan identifies the correct existing Logic
  Model element or elements, or correctly determines that there is no suitable
  existing match. An undisputed mapping or no-match decision scores as correct.
- **No hallucination/toxic content:** whether the summary, rationale, criteria,
  terminology, tone, and other language are accurate, supported, appropriate,
  and free from invented details or overstated claims.

Rejecting only a mapping that MELvan clearly labelled **Low confidence** does
not reduce first-review approval or mapping correctness. Accepting that mapping
confirms it as correct. Evidence reassessed after a Logic Model update creates
another scoring event under the same measures rather than being excluded from
MELvan's overall performance.

The report also records diagnostic learning signals that are not additional
quality scores: mappings MELvan omitted but the Reviewer added; mappings MELvan
proposed but the Reviewer rejected; high-confidence errors, meaning a corrected
or rejected decision originally made with at least 80% confidence; and the
number of tentative mappings presented, accepted, and rejected. These signals
help distinguish important gaps from reasonable low-confidence hypotheses.

Each accepted or corrected decision creates append-only, versioned learning
examples for the relevant dimensions:

- **Classification examples** compare MELvan's proposed RBM level with the
  Reviewer-confirmed level.
- **Direction examples** compare the proposed and confirmed Positive or
  Negative direction for Outcomes.
- **Mapping examples** record each mapping as confirmed, unsupported, or
  omitted. One-to-many decisions are retained element by element.
- **Language examples** record confirmation or structured feedback about an
  inaccurate summary, unsupported or invented detail, overstated causal claim,
  inappropriate terminology or tone, unclear or unnecessarily technical
  language, or another specified language issue. Free-text comments provide
  preferred wording or necessary detail; they do not directly rewrite MELvan's
  global instructions.

MELvan preserves its original proposal and confidence separately from the
Reviewer-confirmed truth. Confirmed truth has full authority for the reviewed
record, but the original confidence remains available to calculate learning
priority. Missing mappings receive the greatest attention. A confidently wrong
decision carries more learning value than a low-confidence error, while a
correct decision near the confidence boundary is more informative than routine
high-confidence confirmation. The objective is better first-review agreement,
not simply increasing confidence scores.

For a new Statement, MELvan retrieves a bounded set of eligible, workspace-
specific precedents using both similarity to the new Statement and learning
priority. Mapping examples must still refer to elements in the current Logic
Model. The analysis records the learning-policy version and the exact examples
used, making their influence auditable. Administrators can disable or roll back
a learning policy and exclude an unsuitable example without deleting its audit
history. MELvan does not fine-tune model weights through this process.

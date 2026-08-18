# MELvan Product Principles

This document records the product commitments that should guide MELvan's design, implementation, and evaluation. They are expressed as principles rather than a feature list so they remain useful as the system evolves.

## 1. Capture evaluation-relevant moments with minimal effort

MELvan exists to help program leaders and other authorized Reporters preserve potentially useful information that would otherwise be lost during daily work. A Reporter can create a Statement from a short note, photo, existing image, voice note, or related sequence of inputs without opening the evaluation framework or deciding where it belongs.

The original Statement is retained. MELvan may structure and summarize it in a Statement analysis, but must not replace it with an untraceable interpretation.

## 2. Classify and map Statements provisionally

MELvan classifies a Statement as an Activity, Output, Outcome, or Indicator. For an Outcome, it assigns Category A, B, or C and one or more specific change types within that category. It maps the Statement to one or more elements in the Logic Model and Performance Measurement Framework (PMF), with confidence assessments and concise rationale.

Only Outcomes have a Positive or Negative direction. Outputs are neutral, as are Activities and Indicators.

Classification and mapping are separate judgments. MELvan searches for semantically and causally suitable existing Logic Model or PMF elements before suggesting anything new. A fresh mapping at 50% confidence or above is presented as supported; a mapping from 25% inclusive to below 50% may be presented only as a visibly distinct **Low confidence** tentative hypothesis, while mappings below 25% are suppressed. The threshold applies independently in one-to-many results, and tentative output remains bounded rather than becoming an unscored list of every possible node. The system must be able to represent a correct classification with an uncertain mapping, a one-to-many mapping, no defensible match, or a possible new Outcome. It must not force a match based on superficial similarity. Outputs are a closed, Administrator-governed catalogue: MELvan can identify an unmatched Output, but it does not create a new Output element.

## 3. Meet a capture-quality contract autonomously

Human review may occur weeks or months after capture. Before an item enters the holding area, MELvan must ensure that it is sufficiently complete, faithful, traceable, and understandable for later review.

The quality contract includes preservation of the source, faithful summarization, available context and metadata, explicit uncertainty, defensible classification and mapping, correct assembly of related inputs, and checks for unsupported or prohibited content. Reporter Context remains separate from the immutable Statement, is revisioned when changed, and identifies the analysis it informed. Failure must remain visible; the system must not disguise uncertainty through polished language.

## 4. Prepare review cycles explicitly

Statements may be submitted at any time, while review meetings are occasional events. Review-ready Statements remain in a temporary holding area until an authorized user creates or opens a review cycle.

A review cycle defines its scope, eligible Statements or Evidence reassessments, framework and classifier versions, and review order. A Reviewer session may end when no eligible work remains or when the Reviewer explicitly ends it; untouched work remains available for a later session. Meeting preparation is distinct from Statement capture and review.

## 5. Support deliberate, item-by-item human review

During a review meeting, Statements may be grouped by theme, category, or framework element and reviewed deliberately. Reviewers can assess independently before discussing discrepancies. The current interface presents all review-ready Statements in one filterable list.

Review considers whether the classification and mapping are correct, whether the summary is faithful, whether anything was hallucinated or harmful, and whether the Statement remains relevant. Acceptance promotes the Statement and current analysis to Evidence only when it has at least one approved Logic Model mapping. A no-suitable-match Statement can be reclassified or marked Not relevant, but only an Administrator can add the Logic Model element needed for later acceptance. Other outcomes include correction, reclassification, remapping, an additional mapping, deferral, program irrelevance, or escalation as a possible framework change. A dual Reporter/Reviewer may review a Statement they submitted after signing out and returning with Reviewer active; the decision is explicitly recorded as a self-review.

Reviewers must be able to make several corrections in one feedback submission. The interface should not force them through a sequence of separate questions for summary, classification, mapping, and relevance.

## 6. Preserve revisions and support reconsideration

Reviewer feedback creates a new Statement-analysis version; it does not erase the Statement, the initial MELvan interpretation, individual Reviewer judgments, or the discussion outcome.

A revised Statement may re-enter the current meeting, return to the holding area for a future review cycle, be marked program-irrelevant without deletion, or be accepted as Evidence. Program-irrelevant records are hidden from every user-facing area and retained only for audit. The full internal history must remain auditable.

Analysis-version history is an internal integrity mechanism. Reviewers encounter only the most recent active Statement analysis and cannot accept or modify a stale analysis.

## 7. Treat the Logic Model and PMF as governed, evolving sources of truth

The Logic Model and PMF are the authoritative program references for relevance, terminology, classification, and mapping, but neither is static. An authorized Administrator builds and maintains the Logic Model entirely within MELvan. Draft changes autosave without affecting processing; **Save changes for later** retains them, and **Commit updates** creates a new immutable aggregate version. Outputs, Outcomes, the Ultimate Goal, and the five single-text RECAP components retain stable identities and their own revision histories. RECAP components are permanent and may be empty; destructive element actions require confirmation, and the Ultimate Goal cannot be deleted. Earlier committed versions can be restored as current without erasing history.

Statements link to versioned analyses and mappings rather than directly to an unversioned framework. Evidence retains its original approved mapping. MELvan preserves mappings whose nodes persist, compares records with new or substantively edited nodes, and searches for a defensible replacement when a mapped node is deleted. Evidence is sent for explicit review only when classification or mapping changes or an individual mapping confidence changes by at least 25 percentage points. Rationale-only differences do not trigger re-review. Every queued reassessment presents a structured **What changed** summary, exact approved assessments are reused after rollback or roll-forward to the same Logic Model version, and historical decisions are never silently rewritten. PMF administration remains a later capability.

## 8. Improve and evaluate MELvan through a separate feedback loop

Feedback about a specific Statement is not automatically a global change to MELvan's rules. Accepted decisions become immutable, program-specific learning examples under a versioned learning policy. Review-cycle findings are aggregated and assessed before an authorized global classifier instruction, configuration, or knowledge source is changed.

Explicit Reviewer corrections are definitive for the Statement analysis. Valid classification and mapping selections are applied directly rather than treated as suggestions for the model to accept or reject. The immutable Statement and system safety boundaries still take precedence.

MELvan is evaluated through explainable scoring events for first-review approval, correct RBM level, correct Outcome direction, correct Logic Model mapping, and absence of hallucinated or toxic content. The interface labels the final measure **No hallucination/toxic content** while retaining the versioned internal scoring key for historical continuity. Direction is scored only for Outcomes. Session and lifetime results show a numerator, denominator, and percentage for every applicable dimension. Structured Reviewer actions are authoritative; free-text comments supplement them when identifying language or quality corrections that are not fully represented by controls. Program-irrelevant Statements and Evidence marked Not relevant for a Logic Model version are excluded from all MELvan metrics. Every material change to classifier or scoring behavior is versioned so performance can be compared over time.

Original model confidence remains distinct from Reviewer-verified truth and learning value. Reviewer acceptance gives the final decision truth weight `1.0` without rewriting the model's original confidence. Learning priority reflects correction type and surprise: omissions and confidently wrong decisions carry more learning value than routine high-confidence confirmation. Learning-informed analyses record the exact examples and learning-policy version used. The objective is better first-review agreement, not higher average confidence. Completed per-session scores remain immutable so self-improvement can be visualized chronologically without rewriting history.

A rejected tentative mapping from 25% inclusive to below 50% confidence is treated as a non-penalizing hypothesis: rejection alone does not reduce first-review approval or mapping correctness. Acceptance counts as a correct mapping. Presented, accepted, and rejected tentative mappings remain separately visible and become workspace-specific learning evidence so the exception cannot conceal speculative behavior.

## 9. Make Evidence useful downstream

Acceptance promotes a Statement and its current analysis to Evidence. Authorized users can retrieve and use Evidence for monitoring, learning, implementation evaluation, outcome evaluation, reporting, and consideration of emergent or unintended consequences.

Downstream use must preserve provenance: the original Statement, internal analysis versions, Reviewer decision, applicable Logic Model and PMF versions, and classifier version remain traceable.

## 10. Make field capture mobile-first

The primary capture experience is optimized for a mobile device and quick, one-handed use. On-device authentication identifies the reporter; the camera can take a new photo; the photo library can supply an existing image; the microphone can capture a voice input for transcription; and a text field supports short notes.

Several inputs may be combined into one observation. Drafts and submissions should tolerate interruption, weak connectivity, retry, and accidental closure. Confirmation must be clear but lightweight. The mobile interface should not require reporters to navigate the Logic Model, PMF, or classification controls at the moment of capture.

Mobile-first applies specifically to the Reporter journey. Reviewer and Administrator workspaces are primarily designed for desktop or laptop use while remaining responsive.

## 11. Keep MELvan purpose-bound

MELvan is not a general-purpose assistant. Its permitted use cases are capturing Statements, structuring and faithfully summarizing them, classifying and mapping them against authorized program frameworks, supporting review and correction, promoting accepted Statements to Evidence, retrieving Evidence for authorized monitoring and evaluation purposes, and administering Reporters and program configurations.

Unsupported requests receive a concise boundary response and a route back to a permitted action. The product uses constrained interactions, explicit supported intents, least-privilege tools and data access, program-specific authorization, and tests that verify both correct operation and resistance to scope expansion.

## 12. Enforce role-based access

Every interaction begins by identifying an active user through a unique email username and password. MELvan uses a 12-character, seven-day temporary password for onboarding, reset, and email-based recovery followed by mandatory replacement. During initial password creation, but not a later password reset, the user must acknowledge the current version of the MELvan User Guide before continuing. The acknowledgement and guide version are retained for audit. Recovery does not disclose account existence and cannot override administrative suspension. User-created passwords meet the published length and character-class rules. Users choose a session ending at midnight tonight, after seven days, or after 28 days, and may always log out earlier. MELvan stores only strong password hashes and revocable server-side sessions. Standard browser form semantics may let a user-controlled password manager store credentials; MELvan never stores plaintext credentials in application browser storage. No program content or workflow is anonymously available.

Exactly one Owner governs the hosted service and creates isolated Administrator workspaces. Administrators manage users and system-managed Logic Models only within their workspace. Reporters create Statements, maintain pre-Evidence Context, and view their own submission history within that workspace. Reviewers assess workspace Statements, promote accepted Statements to Evidence, view workspace Evidence, and reassess materially affected Evidence after a Logic Model change. All authenticated program roles can view their active workspace Logic Model. A person may hold several assigned roles, but each session activates exactly one role and exposes only that role's functionality.

If the Owner suspends an Administrator, access is also suspended for every account created by that Administrator. The workspace and its records remain intact. Reactivating the Administrator restores access only to accounts that are otherwise active; individual suspensions remain authoritative.

The Owner assigns each Administrator workspace a required unique nickname. Nicknames make workspace navigation legible but do not define identity, authorization, ownership, or record relationships; those continue to use immutable internal workspace identifiers.

Authorization determines which records and actions an identity can access. Access is denied by default and enforced by backend operations, workspace-scoped repositories, database constraints, and PostgreSQL row-level security rather than only by the interface. The server derives workspace authority from the session and returns no existence signal for another workspace's identifiers. Users, Logic Models, Statements, Evidence, reviews, exports, scores, and learning memory remain isolated by workspace. Multi-role users choose an active role after identification; changing roles requires sign-out and a new sign-in. Future authentication mechanisms must strengthen rather than bypass this boundary.

## 13. Ground every instance in shared foundational references

Every MELvan instance derives its internal logic from the canonical [foundational references](foundational-references.md): the Global Affairs Canada RBM how-to guide and the W.K. Kellogg Foundation logic-model guide. These sources provide stable definitions, formulation rules, causal reasoning, indicators, and worked examples across programs.

The Global Affairs Canada guide governs MELvan's initial RBM classification and statement-formulation conventions; the Kellogg guide complements it with broader logic-model structure and causal-chain guidance. The active program Logic Model and PMF remain the sources of truth for relevance and mapping. Foundational references, program frameworks, classifier configuration, and Reviewer decisions must retain separate provenance and authority.

Their logic is embodied in MELvan's principles, architecture, classifier rules, prompts, schemas, validation, and regression tests. A separately loaded runtime context pack is not required, and the source documents do not need to be transmitted with each model request. This does not permit model-weight training, automatic rule changes from individual feedback, or sending source documents to an uncontrolled training process. Updating or reinterpreting the canonical references is a governed MELvan product release, not an ordinary program-administration action. A program may add supplementary foundational material, but it may not silently replace, weaken, or override these canonical references.

## 14. Keep health-program Evidence non-clinical and proportionate

MELvan may process program Statements about health services, research, participant experiences, and health-related outcomes, but it remains a program monitoring and evaluation system. It does not diagnose, estimate patient risk or prognosis, recommend treatment, predict treatment response, or infer clinical conditions or biomarkers from text, voice, images, behaviour, or device data.

Reviewer approval makes a Statement program Evidence; it does not establish clinical efficacy, safety, or appropriateness. MELvan preserves source type, attribution, uncertainty, and evidentiary qualifiers so an observation is not rewritten as a validated clinical effect or causal claim.

Health-related use follows the [health-domain safeguards](health-domain-safeguards.md), including default-deny treatment of sensitive patient data, purpose limitation, contextual fairness evaluation, and monitoring under real deployment conditions.

## 15. Make MELvan forkable and operator-owned

MELvan is intended to be a public good. An organization that forks the
repository must be able to configure and operate its own instance without
depending on the original MELvan Azure account, resource identifiers,
credentials, endpoints, domain, Owner identity, or deployment state.

The repository preserves a low-friction SQLite and local-filesystem path for
development and demonstrations while defining PostgreSQL, object storage,
stateless application replicas, migrations, recovery, and observability as the
production architecture. Deployment configuration and secrets remain outside
source code. Operators receive documented procedures for initialization,
upgrade, backup, restore, troubleshooting, and decommissioning.

Public distribution follows an explicit allowlist and clean Git history.
Personally identifiable information, original-deployment account values,
credentials, local machine details, and internal development chronology or implementation
ephemera are kept outside the public repository. Any still-authoritative
requirement is moved into stable product or operator documentation before its
internal source artifact is archived.

A fork inherits source and workflow definitions, not operational trust. Its
operator creates and controls all GitHub environments, variables, secrets,
approvals, deployment identities, OIDC federation, domains, and cloud
resources. An unconfigured fork can run secret-free tests but cannot deploy or
fall back to the original MELvan infrastructure.

Azure is the only maintained cloud deployment. MELvan keeps
infrastructure contracts separate from domain workflows so future maintainers
can add another cloud or self-hosted implementation through an explicitly
tested adapter and deployment profile rather than forking product behavior.

## Cross-cutting commitments

Across all fifteen principles, MELvan must provide:

- Least-privilege access appropriate to role and program.
- Workspace isolation for identities, program records, model context, learning memory, exports, and audit views.
- Versioned records, frameworks, classifier configurations, and decisions.
- Traceability from every interpretation back to its source.
- Explicit uncertainty rather than invented certainty.
- Protection against personal health information, commercially confidential information, and other content prohibited by the applicable program policy.
- An auditable distinction between Statement-analysis revision, classifier improvement, and framework change.
- Traceability from classification and mapping behavior to the canonical foundational references and the versioned classifier and prompt rules that embody them.
- A clear boundary between program Evidence and patient-level clinical inference.
- Operator ownership of configuration, infrastructure, credentials, data,
  backups, and recovery without dependence on the original MELvan deployment.

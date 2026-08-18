# Architecture

## System boundary

MELvan is a role-aware web application for program monitoring, evaluation, and
learning. Its purpose and internal logic are the same in every distribution;
public packaging changes redistribution and configuration, not product intent.
Its bounded workflow is:

1. A Reporter captures a Statement and optional Context.
2. A model adapter produces a versioned analysis and Logic Model mappings.
3. The Statement waits for human review.
4. A Reviewer accepts, corrects, or rejects it as not relevant.
5. Accepted Statements become Evidence.
6. Logic Model changes selectively reassess affected Evidence.
7. Review decisions provide auditable precedents for future analysis.

MELvan does not provide general chat, clinical decision support, autonomous
Evidence acceptance, or silent rewriting of historical records.

## Components

- `web.py` exposes the HTTP API and serves the responsive browser application.
- `service.py` implements Statements, analysis, review, Evidence, Logic Models,
  scoring, and workspace authorization.
- `identity.py` and `auth.py` implement users, roles, passwords, recovery,
  lockout, and server-side sessions.
- `model.py` isolates deterministic, Azure Foundry, and OpenAI-compatible model
  adapters behind one analysis contract.
- `db.py` provides the SQLite development repository.
- `relational.py` provides the SQLAlchemy/PostgreSQL transaction boundary.
- `media.py` provides local and private Azure Blob attachment stores.
- `learning.py` ranks Reviewer-confirmed precedents without changing weights.
- `pdf_export.py` produces workspace-scoped Evidence exports.

## Isolation and authorization

Every program record belongs to an immutable workspace. The server derives the
active role and workspace from a revocable session; it does not trust workspace
identifiers supplied by the browser. PostgreSQL uses composite same-workspace
foreign keys and forced row-level security. Owner observation is read-only.

Exactly one Owner creates Administrators. Each Administrator owns one workspace
and manages its Reporters and Reviewers. Suspending an Administrator also denies
access to accounts in that workspace without deleting their records.

## Records and versioning

Statements are immutable. Context revisions, analyses, mappings, Logic Model
objects, Logic Model versions, review decisions, Evidence assessments, scoring
events, learning examples, and audit events are append-only or versioned.
Interfaces show the current applicable version while preserving provenance.

Logic Model drafts autosave but are not used for analysis until committed.
Activation and rollback create audited state transitions rather than rewriting
committed versions.

The Logic Model and Performance Measurement Framework (PMF) are the two
program-level sources of truth for relevance, terminology, and mapping. The
current application implements Logic Model administration and mapping; PMF
administration and ingestion remain planned. That implementation gap does not
remove the PMF from MELvan's architecture or principles.

## Storage profiles

| Profile | Relational data | Attachments | Intended use |
| --- | --- | --- | --- |
| `local` | SQLite | Local directory | Development and evaluation |
| `hosted_demo` | Local SQLite plus durable snapshot | Azure Files-compatible directory | Existing single-replica demonstration |
| `production` | PostgreSQL | Private Azure Blob Storage | Operator-configured deployment |

The adapters, migrations, and initial PostgreSQL schema exist. The maintained
Azure template deploys `hosted_demo`. Fork operators may configure the
`production` profile within infrastructure designed and validated for their own
requirements.

## Model boundary

The model receives final Reporter-approved text, optional Context, current Logic
Model elements, and selected learning examples. MELvan's versioned processing
rules, prompts, schemas, validation, and tests embody the logic of the canonical
foundational references; a separately loaded runtime context pack is not
required. It returns structured JSON validated by MELvan. Provider output is
never written directly to Evidence. Review remains the authority boundary.

Voice audio is transient. Accepted photo files use opaque workspace-scoped keys
and are authorized through their relational Statement records.

## Reliability

The production relational design uses explicit migrations, a separate migration
role, transaction-scoped workspace context, advisory migration locking, bounded
replay-safe retries, and readiness checks. Attachment migration is resumable and
source-preserving, with size and SHA-256 reconciliation before completion.

## Deployment responsibility

MELvan maintainers are responsible for the core application, its documented
functional behavior, supplied persistence and provider adapters, automated
tests, and accurate deployment instructions for the maintained Azure
configuration.

Each fork operator is responsible for production topology and capacity,
availability, monitoring, incident response, backup and disaster recovery,
data governance, retention, residency, compliance, infrastructure hardening,
and migration of existing data. The project does not certify those operational
choices or provide maintained non-Azure deployment patterns. Built-in email and
password authentication is the currently implemented identity system. A future
external identity-provider adapter must preserve MELvan's role,
workspace-isolation, session, and audit contracts.

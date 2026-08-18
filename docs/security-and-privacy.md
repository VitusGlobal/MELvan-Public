# Security and privacy

## Identity

MELvan uses unique email usernames, strong salted password hashes, a hosted
pepper, revocable server-side sessions, account lockout, and mandatory password
replacement after temporary-password authentication. Public responses do not
reveal whether an account exists.

Initial password creation requires server-validated acknowledgement of the
current MELVan User Guide. MELVan records the guide version in the
authentication audit trail. Recovery sessions are explicitly distinguished
from initial setup and do not repeat the acknowledgement.

Hosted cookies are `HttpOnly`, `Secure`, and `SameSite=Strict`. MELvan does not
store plaintext passwords or bearer tokens in browser storage.

## Authorization

Every protected operation verifies the authenticated user, active role,
workspace, and effective account state at the server. Cross-workspace identifiers
are treated as absent. User-interface hiding is not an authorization control.

The Owner may observe another workspace only in an explicit read-only context.
Suspending an Administrator denies effective access to accounts in that
Administrator's workspace while preserving audit history.

## Program data

Statements, Context, photos, analyses, Reviewer decisions, Evidence, and Logic
Models may contain sensitive program information. Operators are responsible for
data classification, informed use, retention, deletion, residency, access
review, incident response, and applicable legal requirements.

MELvan should not receive patient-level clinical data, credentials, private
keys, or information prohibited by the program's policies. It does not assess
clinical efficacy or provide diagnosis, prognosis, or treatment advice. The
complete, canonical controls are defined in [Health-Domain Safeguards](health-domain-safeguards.md).

## Provider data

Live analysis sends final Statement text, optional Context, relevant program
framework content, and bounded learning examples to the configured model provider. Voice
audio is sent for transcription and immediately discarded. Photos are sent for
text extraction and the normalized accepted image is retained as an attachment.

Operators must review provider retention, regional processing, contractual, and
privacy settings before handling non-synthetic information.

## Secrets

Use managed identity and GitHub OIDC where possible. Store the authentication
pepper and any unavoidable credentials in Key Vault or GitHub secrets. Never
commit populated `.env` files, tokens, connection strings, private keys,
database files, attachments, or exported Evidence.

See [SECURITY.md](../SECURITY.md) for vulnerability reporting.

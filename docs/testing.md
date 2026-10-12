# Testing

## Deterministic suite

Run the complete Python suite without network access:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

The suite covers domain workflows, HTTP contracts, identity, authorization,
workspace isolation, learning, PDF export, attachment storage, migration,
preflight checks, version consistency, and static contracts for both supplied
Azure deployment profiles.

## PostgreSQL and Azurite

With Docker Desktop running:

```bash
docker compose up -d postgres azurite
docker compose --profile check run --rm postgres-check
```

This applies the empty PostgreSQL schema, creates or constrains the runtime
database role, and executes representative relational and attachment tests.
Infrastructure assurance, migration rehearsal against a target Azure
environment, recovery testing, load testing, and replica-capacity validation
remain the fork operator's responsibility.

## Browser tests

`tests/ui-smoke.cjs` contains desktop and narrow-viewport Playwright journeys.
Install the pinned browser-test dependency, then run:

```bash
npm install
npx playwright install --with-deps chromium firefox
npm run test:ui
```

Browser acceptance must verify visible labels, role-specific navigation,
responsive layout, voice and photo states, review actions, and PDF visibility.
Backend tests alone do not close interface requirements.

## Continuous integration

The default GitHub Actions test workflow runs repository-local publication,
unit, PostgreSQL, and Azurite checks. It does not require or contact a live
Azure deployment. The separate Azure deployment workflow is manual-only and
runs only when a repository operator explicitly starts it after configuring
that fork's Azure variables and identity.

## Hosted verification

Use synthetic data in a separately owned Azure environment. For the production
profile, first verify that the migration job succeeds before any web revision is
deployed, at least two replicas can use the same PostgreSQL data, attachments
survive replica replacement in Blob Storage, and the web container has no
migration-owner credential. Then verify login and
recovery email, managed identity, model analysis, speech, OCR, attachment
persistence, restart behavior, authorization boundaries, health probes, custom
domain TLS when configured, and absence of secrets or program data from logs.

This verifies the documented deployment and application behavior. It does not
certify the fork operator's production operations, capacity, monitoring,
backup, disaster recovery, compliance, or data-governance arrangements.

## Publication gate

Before publishing a release candidate:

1. Run `scripts/check-publication.sh`.
2. Run the deterministic and Docker integration suites.
3. Test installation from a fresh clone with no private configuration.
4. Verify every documentation link.
5. Run a maintained secret scanner against the complete public Git history.
6. Confirm the license and redistribution status of every bundled asset.
7. Confirm that product principles, foundational-reference authority, RECAP,
   PMF, and health-domain safeguards match the canonical MELvan release.

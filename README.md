# MELvan

MELvan is a purpose-bound monitoring, evaluation, and learning application for
capturing program Statements, mapping them to a versioned Logic Model and
Performance Measurement Framework, and turning reviewed Statements into Evidence.

MELvan is not a general-purpose assistant and does not evaluate clinical
interventions or patient-level outcomes. Human Reviewers remain responsible for
accepting Evidence and correcting MELvan's classifications and mappings.

## Capabilities

- Text, voice, and photo-based Statement capture with optional Context.
- Activity, Output, Outcome, and Indicator classification.
- One-to-one and one-to-many Logic Model mapping with per-mapping confidence.
- Versioned Logic Models, drafts, rollback, and targeted Evidence reassessment.
- Reporter, Reviewer, Administrator, and Owner roles with workspace isolation.
- Reviewer-informed, auditable learning examples and quality scoring.
- Evidence browsing and PDF export with source-photo attachments.
- SQLite and local attachments for development and demonstrations.
- PostgreSQL and private Azure Blob adapters for the production profile.

## Project status

MELvan is pre-1.0 software. The application workflows are operational, and the
repository includes local, hosted-demonstration, and operator-configured
PostgreSQL/Blob storage profiles. The maintained Azure infrastructure template
deploys the single-replica hosted-demonstration profile; it is an example
deployment, not a prescribed or certified production topology.

Azure is the only maintained cloud platform. Other platforms may be possible,
but they are not documented or tested by this project.

## Responsibility boundary

MELvan maintainers are responsible for the core application, its documented
behavior, supplied adapters, automated tests, and accurate deployment
instructions for the maintained Azure configuration.

Fork operators are responsible for production topology, capacity, availability,
monitoring, incident response, backup and disaster recovery, data governance,
retention, residency, compliance, infrastructure hardening, and migration of
their existing data. These operational responsibilities are outside the scope
of the MELvan project.

## Quick start

MELvan requires Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[foundry]'
cp .env.example .env
```

For a credential-free local evaluation, set `MELVAN_MODEL_MODE=demo` in
`.env`, then run:

```bash
./scripts/start.sh --model-mode demo
```

Open <http://127.0.0.1:8765>.

The initial Owner email is configured with `MELVAN_INITIAL_ADMIN_EMAIL`. Run
the operator-only bootstrap command to establish the first temporary password:

```bash
PYTHONPATH=src .venv/bin/python -m melvan.bootstrap_owner
```

See [Configuration](docs/configuration.md) for live model, email, speech, OCR,
and persistence settings.

## Test

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

With Docker Desktop running, the PostgreSQL and Azurite integration profile is:

```bash
docker compose --profile check run --rm postgres-check
```

## Documentation

- [Product guide](docs/product-guide.md)
- [Product principles](docs/principles.md)
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Azure deployment](docs/azure-deployment.md)
- [Testing](docs/testing.md)
- [Security and privacy](docs/security-and-privacy.md)
- [Canonical foundational references](docs/foundational-references.md)
- [Health-domain safeguards](docs/health-domain-safeguards.md)
- [Contributing](CONTRIBUTING.md)
- [Security reporting](SECURITY.md)

## Licenses

MELvan software is licensed under the [GNU Affero General Public License,
version 3 or later](LICENSE). Documentation is licensed under
[CC BY-SA 4.0](DOCUMENTATION-LICENSE.md), and the MELvan logo is licensed under
[CC BY 4.0](ASSET-LICENSES.md). Third-party components retain their respective
licenses.

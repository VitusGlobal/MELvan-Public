# Contributing

Thank you for considering a contribution to MELvan.

## Development setup

Use Python 3.12, create a project-local virtual environment, install the package
in editable mode, and run the deterministic suite before submitting changes.
See the [README](README.md) and [testing guide](docs/testing.md).

## Change expectations

- Preserve workspace isolation and server-side authorization.
- Keep Statements immutable and retain version/audit provenance.
- Add focused tests for behavior changes and broader tests for shared contracts.
- Update current-state documentation without adding private deployment values.
- Use synthetic identities, Logic Models, Statements, and attachments.
- Never commit secrets, personal data, production exports, or copyrighted source
  material without verified redistribution permission.
- Preserve the title, authority, official URL, version or retrieval date, and
  role of every canonical source that cannot be redistributed as a bundled file.
- Supplementary foundational material may be proposed with provenance, but it
  must not silently replace or weaken MELvan's canonical references, principles,
  RECAP model, or health-domain safeguards.

Submissions that change security boundaries, storage contracts, model inputs,
Evidence rules, or audit behavior should include an architecture decision note.

## License notice

By submitting a contribution, you agree that software contributions are
licensed under the MIT License and documentation contributions are licensed
under CC BY-SA 4.0. You represent that you have the right to submit the work
under those terms.

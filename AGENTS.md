# Contributor guidance

- Keep tenant IDs in every application query and never accept a tenant ID from an untrusted request.
- Use parameterized SQL only. Changes to permission-sensitive behaviour require tests.
- Audit events are append-only: application code must never update or delete `audit_logs`.
- Run `python -m unittest discover -v` before committing.

# Working agreement

- This repository owns the MySQL MCP provider. Consumer applications own their schemas and lifecycle migrations.
- Use Python 3.15 and the official MCP Python SDK. Keep tool registrations statically extractable in server.py, with plain type aliases and dict-literal model_config.
- Deployment controls access. Transaction handles are secret bearer capabilities; do not bind them to caller identity.
- Support existing MySQL 8.4 databases, directly or through the optional Cloud SQL connector. Do not add database provisioning or engine compatibility layers.
- Use personal resources only. Never use Valon resources or ambient Kubernetes credentials.
- Test observable contracts against a local MySQL database. No Docker Compose requirement.
- No unrequested abstractions or explanatory code comments. Keep credentials out of source control.

# MySQL MCP provider

Eight MCP tools for an existing MySQL 8.4 database: `execute`, `select`, `insert`,
`update`, `delete`, `begin`, `commit`, and `rollback`. The public OCI image runs on
Linux arm64 and amd64 and serves Streamable HTTP at `/mcp` on port 3000.

The provider owns connection pooling, SQL execution, table reflection, result
serialization, and short-lived transactions. Applications own tables, migrations,
and business rules. Database installation, provisioning, backups, failover, other
SQL engines, and older MySQL versions are outside this contract.

## Configure

- `DATABASE_URL` (required secret): a SQLAlchemy `mysql+pymysql://user:password@host:3306/database` URL. URL-encode credentials. Driver TLS options can be supplied through the URL query.
- `CLOUD_SQL_INSTANCE` (optional): `project:region:instance`. Uses the Cloud SQL connector with private IP and application default credentials; username, password, and database come from `DATABASE_URL`. The runtime needs network access to that private IP and the Cloud SQL API.

Grant the database user the operations the consuming application needs. Schema
migrations also need DDL privileges. The provider does not create databases or
change database grants.

## Run outside Holon

Run the public image with your database connection supplied through the environment:

```sh
docker run --rm -p 127.0.0.1:3000:3000 --env DATABASE_URL ghcr.io/hughhan1/mysql:0.1.0
```

`DATABASE_URL` must address a database reachable from the container. No Holon
identity is required. Deploy behind your chosen authentication and network access
controls; this provider exposes arbitrary SQL to every client that can reach it.
Production consumers should pin the image digest returned by publication.

## Use in Holon

```yaml
apps:
  - name: mysql
    registry:
      name: io.github.hughhan1/mysql
      version: "0.1.0"
      registry: https://registry.modelcontextprotocol.io
      package: {registryType: oci}
    access:
      callers: [my-app]
      server:
        oauth: {}
    egress:
      - {host: {config: DATABASE_HOST}, ports: [3306]}
```

Declare the same `name`, `version`, and `registry` under `mysql` in the
consumer's `sh.holon.requires`. The catalog and image are public. Supply
`DATABASE_URL` through Holon's secret store. The CLI imports the image into the
installation registry. Egress, `callers`,
and OAuth are deployment policy; this app has no caller allowlist.

## Transactions

Statements without a handle commit individually. `begin` returns a random handle
and an absolute expiry. Handles are **secret bearer capabilities**: anyone with a
handle can use, commit, or roll back that transaction, including another MCP
client. Keep all calls for a handle routed to the same process. Transactions are
not durable across restarts or transferable between replicas.

Handles expire after 30 seconds total or 10 seconds idle. Only one operation per
handle may run at once. Statement failures, expiration, rollback, and shutdown
roll back pending changes and release the connection. MySQL statements with
implicit commits, including DDL, retain MySQL's own transaction semantics.

## Develop

Install Python 3.15 and uv, then `uv sync --locked`. Point `TEST_DATABASE_URL` at a
local MySQL 8.4 test database. Its user needs permission to create and drop
databases named with that database's prefix and an underscore; each worker owns
and cleans up a unique database. No Docker Compose setup is required.

```sh
uv run --locked ruff check
uv run --locked ruff format --check
uv run --locked mypy
uv run --locked pytest -n 2
```

## Publish

Authenticate Docker to GHCR with permission to push `ghcr.io/hughhan1/mysql`,
then publish through the existing Holon CLI:

```sh
holon publish --registry https://registry.modelcontextprotocol.io
```

The CLI authenticates to the official MCP Registry through `mcp-publisher`,
builds and pushes the image and tool contracts, and registers a digest-pinned
descriptor as `io.github.hughhan1/mysql`. Publish using the `hughhan1` GitHub
identity or GitHub Actions OIDC from this repository.

GHCR creates new packages as private. On the first publication, make the new
package public in its GitHub package settings, then rerun the command if registry
validation rejected the private image. The retry reuses the uploaded image.
Subsequent versions retain the package's public visibility.

Existing release tags are reused; change the descriptor, project, and server
versions for a new release. Source, image, and configuration declarations are
public. Actual credentials are supplied at deployment and must never appear in
`server.json`. Consumers outside Holon can use the same descriptor and image with
their own hosting and access controls.

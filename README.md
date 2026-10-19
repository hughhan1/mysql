# MySQL MCP provider

Eight MCP tools for an existing MySQL 8.4 database: `execute`, `select`, `insert`,
`update`, `delete`, `begin`, `commit`, and `rollback`. The private OCI image runs on
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

Authenticate to GHCR with an account that can read this private package, then run:

```sh
docker run --rm -p 127.0.0.1:3000:3000 --env DATABASE_URL ghcr.io/holon-sh/mysql:0.1.0
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
      name: io.github.holon-sh/mysql
      version: "0.1.0"
      registry: https://admin.holon-labs.dev
      package: {registryType: oci}
    access:
      callers: [my-app]
      server:
        oauth: {}
    egress:
      - {host: {config: DATABASE_HOST}, ports: [3306]}
```

Declare the same `name`, `version`, and `registry` under `mysql` in the
consumer's `sh.holon.requires`. Catalog lookup is public. Supply `DATABASE_URL` through Holon's secret
store and authenticate the Holon CLI to GHCR through Docker credentials. The CLI
imports the image into the installation registry. Egress, `callers`,
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

Authenticate Docker to GHCR, then supply an installation control-audience token
with `system:admin:write` through `MCP_REGISTRY_TOKEN`:

```sh
export MCP_REGISTRY_URL=https://admin.holon-labs.dev
holon publish --registry "$MCP_REGISTRY_URL"
```

Publication uploads the private OCI image and registers its digest-pinned MCP
descriptor in Holon's public catalog. Repository and package visibility remain
private. Configuration declarations are public; actual secret values are supplied
at deployment and must never appear in `server.json`.

Existing release tags are reused; change both `server.json` and the project/server
versions for a new release. The catalog is metadata only and grants no permission
to pull the image. Consumers outside Holon can use the same descriptor and private
image with their own hosting and access controls.

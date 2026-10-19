from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.transport_security import TransportSecuritySettings

import server as mysql

SOURCE = "catalog"
routes = {}


def token(subject: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        AccessToken(token=subject, client_id=subject, scopes=[], subject=subject)
    )


@asynccontextmanager
async def hosted() -> AsyncIterator[None]:
    app = mysql.server.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    routes["mysql"] = app
    async with app.router.lifespan_context(app):
        yield
    routes.clear()


@asynccontextmanager
async def connect_http(
    app: str, user: AuthenticatedUser | None = None
) -> AsyncIterator[Client]:
    context = auth_context_var.set(user)
    try:
        async with (
            httpx2.AsyncClient(transport=httpx2.ASGITransport(app=routes[app])) as http,
            Client(
                streamable_http_client(f"http://{app}/mcp", http_client=http),
                mode="2026-07-28",
            ) as client,
        ):
            yield client
    finally:
        auth_context_var.reset(context)


@asynccontextmanager
async def connect(app) -> AsyncIterator[Client]:
    async with Client(app.server, mode="2026-07-28") as client:
        yield client

"""OAuth management commands."""

import typer
from typing import Optional
from pydantic import AnyHttpUrl

from basic_memory.cli.app import app
from basic_memory.mcp.auth_provider import BasicMemoryOAuthProvider
from mcp.shared.auth import OAuthClientInformationFull


auth_app = typer.Typer(help="OAuth client management commands")
app.add_typer(auth_app, name="auth")


@auth_app.command()
def register_client(
    client_id: Optional[str] = typer.Option(
        None, help="Client ID (auto-generated if not provided)"
    ),
    client_secret: Optional[str] = typer.Option(
        None, help="Client secret (auto-generated if not provided)"
    ),
    issuer_url: str = typer.Option("http://localhost:8000", help="OAuth issuer URL"),
):
    """Register a new OAuth client for Basic Memory MCP server."""

    # Create provider instance
    provider = BasicMemoryOAuthProvider(issuer_url=issuer_url)

    # Create client info with required redirect_uris
    client_info = OAuthClientInformationFull(
        client_id=client_id or "",  # Provider will generate if empty
        client_secret=client_secret or "",  # Provider will generate if empty
        redirect_uris=[AnyHttpUrl("http://localhost:8000/callback")],  # Default redirect URI
        client_name="Basic Memory OAuth Client",
        grant_types=["authorization_code", "refresh_token"],
    )

    # Register the client
    import asyncio

    asyncio.run(provider.register_client(client_info))

    typer.echo("Client registered successfully!")
    typer.echo(f"Client ID: {client_info.client_id}")
    typer.echo(f"Client Secret: {client_info.client_secret}")
    typer.echo("\nSave these credentials securely - the client secret cannot be retrieved later.")


@auth_app.command()
def test_auth(
    issuer_url: str = typer.Option("http://localhost:8000", help="OAuth issuer URL"),
):
    """Test OAuth authentication flow.

    IMPORTANT: Use the same FASTMCP_AUTH_SECRET_KEY environment variable
    as your MCP server for tokens to validate correctly.
    """

    import asyncio
    import secrets
    from mcp.server.auth.provider import AuthorizationParams
    from pydantic import AnyHttpUrl

    async def test_flow():
        # Create provider with same secret key as server
        provider = BasicMemoryOAuthProvider(issuer_url=issuer_url)

        # Register a test client
        client_info = OAuthClientInformationFull(
            client_id=secrets.token_urlsafe(16),
            client_secret=secrets.token_urlsafe(32),
            redirect_uris=[AnyHttpUrl("http://localhost:8000/callback")],
            client_name="Test OAuth Client",
            grant_types=["authorization_code", "refresh_token"],
        )
        await provider.register_client(client_info)
        typer.echo(f"Registered test client: {client_info.client_id}")

        # Get the client
        client = await provider.get_client(client_info.client_id)
        if not client:
            typer.echo("Error: Client not found after registration", err=True)
            return

        # Create authorization request
        auth_params = AuthorizationParams(
            state="test-state",
            scopes=["read", "write"],
            code_challenge="test-challenge",
            redirect_uri=AnyHttpUrl("http://localhost:8000/callback"),
            redirect_uri_provided_explicitly=True,
        )

        # Get authorization URL
        auth_url = await provider.authorize(client, auth_params)
        typer.echo(f"Authorization URL: {auth_url}")

        # Extract auth code from URL
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(auth_url)
        params = parse_qs(parsed.query)
        auth_code = params.get("code", [None])[0]

        if not auth_code:
            typer.echo("Error: No authorization code in URL", err=True)
            return

        # Load the authorization code
        code_obj = await provider.load_authorization_code(client, auth_code)
        if not code_obj:
            typer.echo("Error: Invalid authorization code", err=True)
            return

        # Exchange for tokens
        token = await provider.exchange_authorization_code(client, code_obj)
        typer.echo(f"Access token: {token.access_token}")
        typer.echo(f"Refresh token: {token.refresh_token}")
        typer.echo(f"Expires in: {token.expires_in} seconds")

        # Validate access token
        access_token_obj = await provider.load_access_token(token.access_token)
        if access_token_obj:
            typer.echo("Access token validated successfully!")
            typer.echo(f"Client ID: {access_token_obj.client_id}")
            typer.echo(f"Scopes: {access_token_obj.scopes}")
        else:
            typer.echo("Error: Invalid access token", err=True)

    asyncio.run(test_flow())


if __name__ == "__main__":
    auth_app()

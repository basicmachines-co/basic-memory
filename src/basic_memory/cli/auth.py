"""WorkOS OAuth Device Authorization for CLI."""

import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser

import httpx
from rich.console import Console

from basic_memory.config import ConfigManager

console = Console()


class CLIAuth:
    """Handles WorkOS OAuth Device Authorization for CLI tools."""

    def __init__(self, client_id: str, authkit_domain: str):
        self.client_id = client_id
        self.authkit_domain = authkit_domain
        app_config = ConfigManager().config
        # Store tokens in data dir
        self.token_file = app_config.data_dir_path / "basic-memory-cloud.json"
        # PKCE parameters
        self.code_verifier = None
        self.code_challenge = None

    def generate_pkce_pair(self) -> tuple[str, str]:
        """Generate PKCE code verifier and challenge."""
        # Generate code verifier (43-128 characters)
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8")
        code_verifier = code_verifier.rstrip("=")

        # Generate code challenge (SHA256 hash of verifier)
        challenge_bytes = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = base64.urlsafe_b64encode(challenge_bytes).decode("utf-8")
        code_challenge = code_challenge.rstrip("=")

        return code_verifier, code_challenge

    async def request_device_authorization(self) -> dict | None:
        """Request device authorization from WorkOS with PKCE."""
        device_auth_url = f"{self.authkit_domain}/oauth2/device_authorization"

        # Generate PKCE pair
        self.code_verifier, self.code_challenge = self.generate_pkce_pair()

        data = {
            "client_id": self.client_id,
            "scope": "openid profile email offline_access",
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(device_auth_url, data=data)

                if response.status_code == 200:
                    return response.json()
                else:
                    console.print(
                        f"[red]Device authorization failed: {response.status_code} - {response.text}[/red]"
                    )
                    return None
        except Exception as e:
            console.print(f"[red]Device authorization error: {e}[/red]")
            return None

    def display_user_instructions(self, device_response: dict) -> None:
        """Display user instructions for device authorization."""
        user_code = device_response["user_code"]
        verification_uri = device_response["verification_uri"]
        verification_uri_complete = device_response.get("verification_uri_complete")

        console.print("\n[bold blue]Authentication Required[/bold blue]")
        console.print("\nTo authenticate, please visit:")
        console.print(f"[bold cyan]{verification_uri}[/bold cyan]")
        console.print(f"\nAnd enter this code: [bold yellow]{user_code}[/bold yellow]")

        if verification_uri_complete:
            console.print("\nOr for one-click access, visit:")
            console.print(f"[bold green]{verification_uri_complete}[/bold green]")

            # Try to open browser automatically
            try:
                console.print("\n[dim]Opening browser automatically...[/dim]")
                webbrowser.open(verification_uri_complete)
            except Exception:
                pass  # Silently fail if browser can't be opened

        console.print("\n[dim]Waiting for you to complete authentication in your browser...[/dim]")

    async def poll_for_token(self, device_code: str, interval: int = 5) -> dict | None:
        """Poll the token endpoint until user completes authentication."""
        token_url = f"{self.authkit_domain}/oauth2/token"

        data = {
            "client_id": self.client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "code_verifier": self.code_verifier,
        }

        max_attempts = 60  # 5 minutes with 5-second intervals
        current_interval = interval

        for _attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(token_url, data=data)

                    if response.status_code == 200:
                        return response.json()

                    # Parse error response
                    try:
                        error_data = response.json()
                        error = error_data.get("error")
                    except Exception:
                        error = "unknown_error"

                    if error == "authorization_pending":
                        # User hasn't completed auth yet, keep polling
                        pass
                    elif error == "slow_down":
                        # Increase polling interval
                        current_interval += 5
                        console.print("[yellow]Slowing down polling rate...[/yellow]")
                    elif error == "access_denied":
                        console.print("[red]Authentication was denied by user[/red]")
                        return None
                    elif error == "expired_token":
                        console.print("[red]Device code has expired. Please try again.[/red]")
                        return None
                    else:
                        console.print(f"[red]Token polling error: {error}[/red]")
                        return None

            except Exception as e:
                console.print(f"[red]Token polling request error: {e}[/red]")

            # Wait before next poll
            await self._async_sleep(current_interval)

        console.print("[red]Authentication timeout. Please try again.[/red]")
        return None

    async def _async_sleep(self, seconds: int) -> None:
        """Async sleep utility."""
        import asyncio

        await asyncio.sleep(seconds)

    def save_tokens(self, tokens: dict) -> None:
        """Save tokens to project root as .bm-auth.json."""
        token_data = {
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "expires_at": int(time.time()) + tokens.get("expires_in", 3600),
            "token_type": tokens.get("token_type", "Bearer"),
        }

        with open(self.token_file, "w") as f:
            json.dump(token_data, f, indent=2)

        # Secure the token file
        os.chmod(self.token_file, 0o600)

        console.print(f"[green]Tokens saved to {self.token_file}[/green]")

    def load_tokens(self) -> dict | None:
        """Load tokens from .bm-auth.json file."""
        if not self.token_file.exists():
            return None

        try:
            with open(self.token_file) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def is_token_valid(self, tokens: dict) -> bool:
        """Check if stored token is still valid."""
        expires_at = tokens.get("expires_at", 0)
        # Add 60 second buffer for clock skew
        return time.time() < (expires_at - 60)

    async def refresh_token(self, refresh_token: str) -> dict | None:
        """Refresh access token using refresh token."""
        token_url = f"{self.authkit_domain}/oauth2/token"

        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(token_url, data=data)

                if response.status_code == 200:
                    return response.json()
                else:
                    console.print(
                        f"[red]Token refresh failed: {response.status_code} - {response.text}[/red]"
                    )
                    return None
        except Exception as e:
            console.print(f"[red]Token refresh error: {e}[/red]")
            return None

    async def get_valid_token(self) -> str | None:
        """Get valid access token, refresh if needed."""
        tokens = self.load_tokens()
        if not tokens:
            return None

        if self.is_token_valid(tokens):
            return tokens["access_token"]

        # Token expired - try to refresh if we have a refresh token
        refresh_token = tokens.get("refresh_token")
        if refresh_token:
            console.print("[yellow]Access token expired, refreshing...[/yellow]")

            new_tokens = await self.refresh_token(refresh_token)
            if new_tokens:
                # Save new tokens (may include rotated refresh token)
                self.save_tokens(new_tokens)
                console.print("[green]Token refreshed successfully[/green]")
                return new_tokens["access_token"]
            else:
                console.print("[yellow]Token refresh failed. Please run 'login' again.[/yellow]")
                return None
        else:
            console.print("[yellow]No refresh token available. Please run 'login' again.[/yellow]")
            return None

    async def login(self) -> bool:
        """Perform OAuth Device Authorization login flow."""
        console.print("[blue]Initiating authentication...[/blue]")

        # Step 1: Request device authorization
        device_response = await self.request_device_authorization()
        if not device_response:
            return False

        # Step 2: Display user instructions
        self.display_user_instructions(device_response)

        # Step 3: Poll for token
        device_code = device_response["device_code"]
        interval = device_response.get("interval", 5)

        tokens = await self.poll_for_token(device_code, interval)
        if not tokens:
            return False

        # Step 4: Save tokens
        self.save_tokens(tokens)

        console.print("\n[green]Successfully authenticated with Basic Memory Cloud![/green]")
        return True

    def logout(self) -> None:
        """Remove stored authentication tokens."""
        if self.token_file.exists():
            self.token_file.unlink()
            console.print("[green]Logged out successfully[/green]")
        else:
            console.print("[yellow]No stored authentication found[/yellow]")

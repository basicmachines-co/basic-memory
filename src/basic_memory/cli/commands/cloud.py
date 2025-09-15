"""Command module for basic-memory cloud operations."""

import asyncio
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

from basic_memory.cli.app import cloud_app
from basic_memory.cli.auth import CLIAuth
from basic_memory.config import ConfigManager
from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.utils import generate_permalink

console = Console()


class CloudAPIError(Exception):
    """Exception raised for cloud API errors."""

    pass


def get_cloud_config() -> tuple[str, str, str]:
    """Get cloud OAuth configuration from config."""
    config_manager = ConfigManager()
    config = config_manager.config
    return config.cloud_client_id, config.cloud_domain, config.cloud_host


async def make_api_request(
    method: str,
    url: str,
    headers: Optional[dict] = None,
    json_data: Optional[dict] = None,
    timeout: float = 30.0,
) -> httpx.Response:
    """Make an API request to the cloud service."""
    headers = headers or {}
    auth_headers = await get_authenticated_headers()
    headers.update(auth_headers)
    # Add debug headers to help with compression issues
    headers.setdefault("Accept-Encoding", "identity")  # Disable compression for debugging

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            console.print(f"[dim]Making {method} request to {url}[/dim]")
            console.print(f"[dim]Headers: {dict(headers)}[/dim]")

            response = await client.request(method=method, url=url, headers=headers, json=json_data)

            console.print(f"[dim]Response status: {response.status_code}[/dim]")
            console.print(f"[dim]Response headers: {dict(response.headers)}[/dim]")

            response.raise_for_status()
            return response
        except httpx.HTTPError as e:
            console.print(f"[red]HTTP Error details: {e}[/red]")
            # Check if this is a response error with response details
            if hasattr(e, "response") and e.response is not None:  # pyright: ignore [reportAttributeAccessIssue]
                response = e.response  # type: ignore
                console.print(f"[red]Response status: {response.status_code}[/red]")
                console.print(f"[red]Response headers: {dict(response.headers)}[/red]")
                try:
                    console.print(f"[red]Response text: {response.text}[/red]")
                except Exception:
                    console.print("[red]Could not read response text[/red]")
            raise CloudAPIError(f"API request failed: {e}") from e


async def get_authenticated_headers() -> dict[str, str]:
    """Get authentication headers with JWT token."""
    client_id, domain, _ = get_cloud_config()
    auth = CLIAuth(client_id=client_id, authkit_domain=domain)
    token = await auth.get_valid_token()
    if not token:
        console.print("[red]Not authenticated. Please run 'tenant login' first.[/red]")
        raise typer.Exit(1)

    return {"Authorization": f"Bearer {token}"}


@cloud_app.command()
def login():
    """Authenticate with WorkOS using OAuth Device Authorization flow."""

    async def _login():
        client_id, domain, _ = get_cloud_config()
        auth = CLIAuth(client_id=client_id, authkit_domain=domain)

        success = await auth.login()
        if not success:
            console.print("[red]Login failed[/red]")
            raise typer.Exit(1)

    asyncio.run(_login())


# Project

project_app = typer.Typer(help="Manage Basic Memory Cloud Projects")
cloud_app.add_typer(project_app, name="project")


@project_app.command("list")
def list_projects() -> None:
    """List projects on the cloud instance."""

    try:
        # Get cloud configuration
        _, _, host_url = get_cloud_config()
        host_url = host_url.rstrip("/")

        console.print(f"[blue]Fetching projects from {host_url}...[/blue]")

        # Make API request to list projects
        response = asyncio.run(
            make_api_request(method="GET", url=f"{host_url}/proxy/projects/projects")
        )

        projects_data = response.json()

        if not projects_data.get("projects"):
            console.print("[yellow]No projects found on the cloud instance.[/yellow]")
            return

        # Create table for display
        table = Table(
            title="Cloud Projects", show_header=True, header_style="bold blue", min_width=60
        )
        table.add_column("Name", style="green", min_width=20)
        table.add_column("Path", style="dim", min_width=30)

        for project in projects_data["projects"]:
            # Format the path for display
            path = project.get("path", "")
            if path.startswith("/"):
                path = f"~{path}" if path.startswith(str(Path.home())) else path

            table.add_row(
                project.get("name", "unnamed"),
                path,
            )

        console.print(table)
        console.print(f"\n[green]Found {len(projects_data['projects'])} project(s)[/green]")

    except CloudAPIError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)


@project_app.command("add")
def add_project(
    name: str = typer.Argument(..., help="Name of the project to add"),
    set_default: bool = typer.Option(False, "--default", "-d", help="Set as default project"),
) -> None:
    """Create a new project on the cloud instance."""

    # Get cloud configuration
    _, _, host_url = get_cloud_config()
    host_url = host_url.rstrip("/")

    # Prepare headers
    headers = {"Content-Type": "application/json"}

    project_path = generate_permalink(name)
    # Prepare project data
    project_data = {
        "name": name,
        "path": project_path,
        "set_default": set_default,
    }

    console.print(project_data)

    try:
        console.print(f"[blue]Creating project '{name}' on {host_url}...[/blue]")

        # Make API request to create project
        response = asyncio.run(
            make_api_request(
                method="POST",
                url=f"{host_url}/proxy/projects/projects",
                headers=headers,
                json_data=project_data,
            )
        )

        result = response.json()

        console.print(f"[green]Project '{name}' created successfully![/green]")

        # Display project details
        if "project" in result:
            project = result["project"]
            console.print(f"  Name: {project.get('name', name)}")
            console.print(f"  Path: {project.get('path', 'unknown')}")
            if project.get("id"):
                console.print(f"  ID: {project['id']}")

    except CloudAPIError as e:
        console.print(f"[red]Error creating project: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)


@cloud_app.command("upload")
def upload_files(
    project: str = typer.Argument(..., help="Project name to upload to"),
    path_to_files: str = typer.Argument(..., help="Local path to files or directory to upload"),
    preserve_timestamps: bool = typer.Option(
        True,
        "--preserve-timestamps/--no-preserve-timestamps",
        help="Preserve file modification times",
    ),
    respect_gitignore: bool = typer.Option(
        True,
        "--respect-gitignore/--no-gitignore",
        help="Respect .gitignore patterns and skip common development artifacts",
    ),
) -> None:
    """Upload files to a cloud project using WebDAV."""

    # Get cloud configuration
    _, _, host_url = get_cloud_config()
    host_url = host_url.rstrip("/")

    # Validate local path
    local_path = Path(path_to_files).expanduser().resolve()
    if not local_path.exists():
        console.print(f"[red]Error: Path '{path_to_files}' does not exist[/red]")
        raise typer.Exit(1)

    # Prepare headers
    headers = {}

    try:
        # Load gitignore patterns (only if enabled)
        ignore_patterns = load_gitignore_patterns(local_path) if respect_gitignore else set()

        # Collect files to upload
        files_to_upload = []
        ignored_count = 0

        if local_path.is_file():
            # Single file upload - check if it should be ignored
            if not respect_gitignore or not should_ignore_path(
                local_path, local_path.parent, ignore_patterns
            ):
                files_to_upload.append(local_path)
            else:
                ignored_count += 1
        else:
            # Recursively collect all files
            for file_path in local_path.rglob("*"):
                if file_path.is_file():
                    if not respect_gitignore or not should_ignore_path(
                        file_path, local_path, ignore_patterns
                    ):
                        files_to_upload.append(file_path)
                    else:
                        ignored_count += 1

        # Show summary
        if ignored_count > 0 and respect_gitignore:
            console.print(
                f"[dim]Ignored {ignored_count} file(s) based on .gitignore and default patterns[/dim]"
            )

        if not files_to_upload:
            console.print("[yellow]No files found to upload[/yellow]")
            return

        console.print(
            f"[blue]Uploading {len(files_to_upload)} file(s) to project '{project}' on {host_url}...[/blue]"
        )

        # Upload files using WebDAV
        asyncio.run(
            _upload_files_webdav(
                files_to_upload=files_to_upload,
                local_base_path=local_path,
                project=project,
                host_url=host_url,
                headers=headers,
                preserve_timestamps=preserve_timestamps,
            )
        )

        console.print(f"[green]Successfully uploaded {len(files_to_upload)} file(s)![/green]")

    except CloudAPIError as e:
        console.print(f"[red]Error uploading files: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)


async def _upload_files_webdav(
    files_to_upload: list[Path],
    local_base_path: Path,
    project: str,
    host_url: str,
    headers: dict,
    preserve_timestamps: bool,
) -> None:
    """Upload files using WebDAV protocol."""

    # Get authentication headers for WebDAV uploads
    auth_headers = await get_authenticated_headers()

    async with httpx.AsyncClient(timeout=300.0) as client:
        for file_path in files_to_upload:
            # Calculate relative path for WebDAV outside try block
            if local_base_path.is_file():
                # Single file upload - use just the filename
                relative_path = file_path.name
            else:
                # Directory upload - preserve structure
                relative_path = file_path.relative_to(local_base_path)

            try:
                # WebDAV URL
                webdav_url = f"{host_url}/proxy/{project}/webdav/{relative_path}"

                # Prepare upload headers
                upload_headers = dict(headers)
                upload_headers.update(auth_headers)

                # Add timestamp preservation header if requested
                if preserve_timestamps:
                    mtime = file_path.stat().st_mtime
                    upload_headers["X-OC-Mtime"] = str(mtime)

                # Disable compression for WebDAV as well
                upload_headers.setdefault("Accept-Encoding", "identity")

                # Read file content
                file_content = file_path.read_bytes()

                # console.print(f"[dim]Uploading {relative_path} to {webdav_url}[/dim]")

                # Upload file
                response = await client.put(
                    webdav_url, content=file_content, headers=upload_headers
                )

                # console.print(f"[dim]WebDAV response status: {response.status_code}[/dim]")
                response.raise_for_status()

                # Show file upload progress
                console.print(f"  ✓ {relative_path}")

            except httpx.HTTPError as e:
                console.print(f"  ✗ {relative_path} - {e}")
                if hasattr(e, "response") and e.response is not None:  # pyright: ignore [reportAttributeAccessIssue]
                    response = e.response  # type: ignore
                    console.print(f"[red]WebDAV Response status: {response.status_code}[/red]")
                    console.print(f"[red]WebDAV Response headers: {dict(response.headers)}[/red]")
                raise CloudAPIError(f"Failed to upload {file_path.name}: {e}") from e


@cloud_app.command("status")
def status() -> None:
    """Check the status of the cloud instance."""

    # Get cloud configuration
    _, _, host_url = get_cloud_config()
    host_url = host_url.rstrip("/")

    # Prepare headers
    headers = {}

    try:
        console.print(f"[blue]Checking status of {host_url}...[/blue]")

        # Make API request to check health
        response = asyncio.run(
            make_api_request(method="GET", url=f"{host_url}/proxy/health", headers=headers)
        )

        health_data = response.json()

        console.print("[green]Cloud instance is healthy[/green]")

        # Display status details
        if "status" in health_data:
            console.print(f"  Status: {health_data['status']}")
        if "version" in health_data:
            console.print(f"  Version: {health_data['version']}")
        if "timestamp" in health_data:
            console.print(f"  Timestamp: {health_data['timestamp']}")

    except CloudAPIError as e:
        console.print(f"[red]Error checking status: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)

"""Command module for basic-memory cloud operations."""

import asyncio
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    FileSizeColumn,
    TotalFileSizeColumn,
    TransferSpeedColumn,
)

from basic_memory.cli.app import cloud_app
from basic_memory.cli.auth import CLIAuth
from basic_memory.utils import generate_permalink

console = Console()


class CloudAPIError(Exception):
    """Exception raised for cloud API errors."""

    pass

# TODO this is the workos dev env
CLI_OAUTH_CLIENT_ID="client_01K46RED2BW9YKYE4N7Y9BDN2V"
AUTHKIT_DOMAIN="https://exciting-aquarium-32-staging.authkit.app"


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
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.request(
                method=method, url=url, headers=headers, json=json_data
            )
            console.print(response)
            response.raise_for_status()
            return response
        except httpx.HTTPError as e:
            raise CloudAPIError(f"API request failed: {e}") from e

async def get_authenticated_headers() -> dict[str, str]:
    """Get authentication headers with JWT token."""
    auth = CLIAuth(client_id=CLI_OAUTH_CLIENT_ID, authkit_domain=AUTHKIT_DOMAIN)
    token = await auth.get_valid_token()
    if not token:
        console.print("[red]Not authenticated. Please run 'tenant login' first.[/red]")
        raise typer.Exit(1)

    return {"Authorization": f"Bearer {token}"}


@cloud_app.command()
def login():
    """Authenticate with WorkOS using OAuth Device Authorization flow."""

    async def _login():
        auth = CLIAuth(
            client_id=CLI_OAUTH_CLIENT_ID, authkit_domain=AUTHKIT_DOMAIN
        )

        success = await auth.login()
        if not success:
            console.print("[red]Login failed[/red]")
            raise typer.Exit(1)

    asyncio.run(_login())

@cloud_app.command("list")
def list_projects(
    host_url: str = typer.Option(..., "--host", "-h", help="Cloud host URL"),
) -> None:
    """List projects on the cloud instance."""

    # Clean up the host URL
    host_url = host_url.rstrip("/")

    try:
        console.print(f"[blue]Fetching projects from {host_url}...[/blue]")

        # Make API request to list projects
        response = asyncio.run(
            make_api_request(method="GET", url=f"{host_url}/projects/projects")
        )

        projects_data = response.json()

        if not projects_data.get("projects"):
            console.print("[yellow]No projects found on the cloud instance.[/yellow]")
            return

        # Create table for display
        table = Table(title=f"Projects on {host_url}", show_header=True, header_style="bold blue")
        table.add_column("Name", style="green")
        table.add_column("Path", style="dim")

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


@cloud_app.command("create")
def create_project(
    name: str = typer.Argument(..., help="Name of the project to create"),
    host_url: str = typer.Option(..., "--host", "-h", help="Cloud host URL"),
    set_default: bool = typer.Option(False, "--default", "-d", help="Set as default project"),
) -> None:
    """Create a new project on the cloud instance."""

    # Clean up the host URL
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
                url=f"{host_url}/projects/projects",
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
    host_url: str = typer.Option(..., "--host", "-h", help="Cloud host URL"),
    preserve_timestamps: bool = typer.Option(
        True,
        "--preserve-timestamps/--no-preserve-timestamps",
        help="Preserve file modification times",
    ),
) -> None:
    """Upload files to a cloud project using WebDAV."""

    # Clean up the host URL
    host_url = host_url.rstrip("/")

    # Validate local path
    local_path = Path(path_to_files).expanduser().resolve()
    if not local_path.exists():
        console.print(f"[red]Error: Path '{path_to_files}' does not exist[/red]")
        raise typer.Exit(1)

    # Prepare headers
    headers = {}

    try:
        # Collect files to upload
        files_to_upload = []

        if local_path.is_file():
            files_to_upload.append(local_path)
        else:
            # Recursively collect all files
            for file_path in local_path.rglob("*"):
                if file_path.is_file():
                    files_to_upload.append(file_path)

        if not files_to_upload:
            console.print("[yellow]No files found to upload[/yellow]")
            return

        console.print(
            f"[blue]Uploading {len(files_to_upload)} file(s) to project '{project}' on {host_url}...[/blue]"
        )

        # Calculate total size for progress tracking
        total_size = sum(f.stat().st_size for f in files_to_upload)

        # Create progress bar
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            FileSizeColumn(),
            "/",
            TotalFileSizeColumn(),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task("Uploading files...", total=total_size)

            # Upload files using WebDAV
            asyncio.run(
                _upload_files_webdav(
                    files_to_upload=files_to_upload,
                    local_base_path=local_path,
                    project=project,
                    host_url=host_url,
                    headers=headers,
                    preserve_timestamps=preserve_timestamps,
                    progress=progress,
                    task=task,
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
    progress: Progress,
    task,
) -> None:
    """Upload files using WebDAV protocol."""

    async with httpx.AsyncClient(timeout=300.0) as client:
        for file_path in files_to_upload:
            try:
                # Calculate relative path for WebDAV
                if local_base_path.is_file():
                    # Single file upload - use just the filename
                    relative_path = file_path.name
                else:
                    # Directory upload - preserve structure
                    relative_path = file_path.relative_to(local_base_path)

                # WebDAV URL
                webdav_url = f"{host_url}/{project}/webdav/{relative_path}"

                # Prepare upload headers
                upload_headers = dict(headers)

                # Add timestamp preservation header if requested
                if preserve_timestamps:
                    mtime = file_path.stat().st_mtime
                    upload_headers["X-OC-Mtime"] = str(mtime)

                # Read file content
                file_content = file_path.read_bytes()

                # Upload file
                response = await client.put(
                    webdav_url, content=file_content, headers=upload_headers
                )

                response.raise_for_status()

                # Update progress
                progress.update(task, advance=len(file_content))

            except httpx.HTTPError as e:
                raise CloudAPIError(f"Failed to upload {file_path.name}: {e}") from e


@cloud_app.command("status")
def status(
    host_url: str = typer.Option(..., "--host", "-h", help="Cloud host URL"),
) -> None:
    """Check the status of the cloud instance."""

    # Clean up the host URL
    host_url = host_url.rstrip("/")

    # Prepare headers
    headers = {}

    try:
        console.print(f"[blue]Checking status of {host_url}...[/blue]")

        # Make API request to check health
        response = asyncio.run(
            make_api_request(method="GET", url=f"{host_url}/health", headers=headers)
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

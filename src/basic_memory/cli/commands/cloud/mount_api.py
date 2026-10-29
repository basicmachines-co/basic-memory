"""Cloud mount API calls for Personal rclone transfers.

`bm cloud setup` mints bucket-scoped storage credentials through these calls,
and the Personal `push`/`pull`/`prune`/`check` commands read the tenant's
bucket name from the mount info endpoint.
"""

from basic_memory.cli.commands.cloud.api_client import make_api_request
from basic_memory.config import ConfigManager
from basic_memory.schemas.cloud import MountCredentials, TenantMountInfo


class MountApiError(Exception):
    """Exception raised when a cloud mount API call fails."""

    pass


def _workspace_id_header(workspace_id: str | None) -> dict[str, str]:
    """Header that routes a /tenant/mount/* request to a specific tenant.

    The mount endpoints resolve the workspace from X-Workspace-ID (validating
    membership + subscription) and fall back to the user's default tenant when
    it is absent — so omitting it preserves the original default-tenant behavior.
    """
    return {"X-Workspace-ID": workspace_id} if workspace_id else {}


async def get_mount_info(*, workspace_id: str | None = None) -> TenantMountInfo:
    """Get tenant mount info (bucket name + tenant id) from the cloud API.

    Args:
        workspace_id: Tenant id of the target workspace. When omitted, the API
            uses the authenticated user's default tenant.
    """
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        response = await make_api_request(
            method="GET",
            url=f"{host_url}/tenant/mount/info",
            headers=_workspace_id_header(workspace_id),
        )

        return TenantMountInfo.model_validate(response.json())
    except Exception as e:
        raise MountApiError(f"Failed to get tenant info: {e}") from e


async def generate_mount_credentials(tenant_id: str) -> MountCredentials:
    """Generate scoped S3 credentials for transfers against a tenant's bucket.

    Args:
        tenant_id: Tenant id whose bucket-scoped credentials to mint. Routed via
            X-Workspace-ID so team workspaces get their own bucket's credentials.
    """
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        # The mount endpoints resolve X-Workspace-ID by matching the workspace's
        # tenant_id, so passing a tenant_id here is the correct routing key.
        response = await make_api_request(
            method="POST",
            url=f"{host_url}/tenant/mount/credentials",
            headers=_workspace_id_header(tenant_id),
        )

        return MountCredentials.model_validate(response.json())
    except Exception as e:
        raise MountApiError(f"Failed to generate credentials: {e}") from e

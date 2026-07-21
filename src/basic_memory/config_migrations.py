"""Legacy Basic Memory configuration migrations."""

import os
from typing import Any


def migrate_legacy_sync_fields(
    data: Any,
    *,
    legacy_fields: dict[str, str],
    env_prefix: str,
) -> Any:
    """Map legacy sync field names while preserving new-name precedence."""
    if not isinstance(data, dict):
        return data
    for new_field, legacy_key in legacy_fields.items():
        if new_field in data:
            continue
        legacy_env_value = os.getenv(f"{env_prefix}{legacy_key.upper()}")
        if legacy_env_value is not None:
            data[new_field] = legacy_env_value
        elif legacy_key in data:
            data[new_field] = data[legacy_key]
    return data


def migrate_legacy_projects(data: Any) -> Any:
    """Convert legacy project dictionaries into unified project entries."""
    if not isinstance(data, dict):
        return data

    data.pop("default_project_mode", None)
    data.pop("cloud_mode", None)

    projects = data.get("projects", {})
    if not projects:
        return data

    first_value = next(iter(projects.values()), None)
    if isinstance(first_value, str):
        project_modes = data.pop("project_modes", {})
        cloud_projects = data.pop("cloud_projects", {})
        new_projects: dict[str, Any] = {}
        for name, path in projects.items():
            entry: dict[str, Any] = {"path": path}
            if name in project_modes:
                entry["mode"] = project_modes[name]
            if name in cloud_projects:
                cloud_project = cloud_projects[name]
                if isinstance(cloud_project, dict):
                    entry["local_sync_path"] = cloud_project.get("local_path")
                    entry["bisync_initialized"] = cloud_project.get("bisync_initialized", False)
                    entry["last_sync"] = cloud_project.get("last_sync")
                else:
                    entry["local_sync_path"] = getattr(cloud_project, "local_path", None)
                    entry["bisync_initialized"] = getattr(
                        cloud_project, "bisync_initialized", False
                    )
                    entry["last_sync"] = getattr(cloud_project, "last_sync", None)
            new_projects[name] = entry

        # A cloud-only legacy entry still needs a unified project record even
        # when the old projects mapping did not contain it.
        for name, cloud_project in cloud_projects.items():
            if name not in new_projects and isinstance(cloud_project, dict):
                local_path = cloud_project.get("local_path", "")
                new_projects[name] = {
                    "path": local_path or "",
                    "mode": project_modes.get(name, "cloud"),
                    "local_sync_path": local_path,
                    "bisync_initialized": cloud_project.get("bisync_initialized", False),
                    "last_sync": cloud_project.get("last_sync"),
                }

        data["projects"] = new_projects
    else:
        data.pop("project_modes", None)
        data.pop("cloud_projects", None)

    # Cloud project paths from old releases were remote slugs. A configured
    # local sync path is the canonical local filesystem path in the new model.
    projects = data.get("projects", {})
    for entry in projects.values():
        if isinstance(entry, dict):
            local_sync_path = entry.get("local_sync_path")
            path = entry.get("path", "")
            if local_sync_path and not os.path.isabs(path):
                entry["path"] = local_sync_path

    return data

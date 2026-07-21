"""Compatibility imports for the retired lifecycle-event projector.

Lifecycle trace is no longer promoted into graph notes. New code should import
the local archive sweep from :mod:`basic_memory.hooks.archive` and project-ref
routing from :mod:`basic_memory.hooks.project_ref`.
"""

from basic_memory.hooks.archive import FlushResult, flush
from basic_memory.hooks.project_ref import UUID_RE, split_project_ref

__all__ = ["FlushResult", "UUID_RE", "flush", "split_project_ref"]

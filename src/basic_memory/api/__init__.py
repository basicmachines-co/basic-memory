"""Basic Memory API module."""

import sys
from types import ModuleType
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["app"]


def __getattr__(name: str) -> "FastAPI":
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    # Container-only imports must not build the local API and its routers.
    from .app import app

    # Importing the submodule installs it as package.app; cache the instance instead.
    globals()["app"] = app
    return app


class _ApiModule(ModuleType):
    @override
    def __setattr__(self, name: str, value: object) -> None:
        # A direct submodule import bypasses __getattr__ and installs the module
        # as package.app. Preserve the public FastAPI export in that order too.
        if name == "app" and isinstance(value, ModuleType):
            from .app import app

            value = app
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _ApiModule

"""Plugin loader.

A plugin is a ``.py`` file (or package) in ``<data>/plugins`` or ``<app>/plugins``
exposing ``register(api: PluginAPI)``. Files starting with ``_`` are ignored.
Plugins can add download providers, upscale backends, API routes and event
listeners — see ``plugins/_example_plugin.py``.
"""
from __future__ import annotations

import importlib.util
import sys
from typing import Any, Callable

from fastapi import APIRouter

from .config import app_root, paths
from .events import bus
from .logging_setup import get_logger

log = get_logger("app")


class PluginAPI:
    def __init__(self, app) -> None:
        self._app = app
        self.log = log

    def add_provider(self, provider) -> None:
        from .downloader.registry import registry
        registry.register(provider)

    def add_upscale_backend(self, backend) -> None:
        from .upscale.engine import register_backend
        register_backend(backend)

    def add_router(self, router: APIRouter, prefix: str) -> None:
        self._app.include_router(router, prefix=f"/api/plugins/{prefix.strip('/')}")

    def on_event(self, callback: Callable[[dict[str, Any]], None]) -> None:
        bus.add_listener(callback)


def load_plugins(app) -> list[dict]:
    loaded = []
    api = PluginAPI(app)
    for root in (app_root() / "plugins", paths().plugins):
        if not root.exists():
            continue
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(("_", ".")):
                continue
            target = entry / "__init__.py" if entry.is_dir() else entry
            if target.suffix != ".py" or not target.exists():
                continue
            name = f"medialab_plugin_{entry.stem}"
            try:
                spec = importlib.util.spec_from_file_location(name, target)
                assert spec and spec.loader
                mod = importlib.util.module_from_spec(spec)
                sys.modules[name] = mod
                spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register(api)
                loaded.append({"name": getattr(mod, "PLUGIN_NAME", entry.stem), "path": str(entry), "ok": True})
                log.info("Plugin loaded: %s", entry.name)
            except Exception as e:
                log.exception("Plugin %s failed to load", entry.name)
                loaded.append({"name": entry.stem, "path": str(entry), "ok": False, "error": str(e)})
    return loaded


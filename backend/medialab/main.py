"""Entry point.

    python -m medialab            desktop app (WebView2 window + local API)
    python -m medialab --server   API only (open the UI in a browser / Vite dev)
    python -m medialab --dev      API on port 8765 with token "dev" for `npm run dev`
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import APP_NAME, components
from .config import paths


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _single_instance_or_focus(files: list[str] | None = None) -> bool:
    """Return False if another instance is already running (and focus it, handing it ``files``)."""
    from .security import TOKEN_HEADER, read_session_file
    info = read_session_file()
    if not info or info.get("shell") is False:  # a server / dev instance without a window: open se7en normally
        return True
    try:
        import httpx
        r = httpx.get(f"http://127.0.0.1:{info['port']}/api/health", timeout=1.5)
        if r.status_code == 200:
            if files:  # "Open with se7en" while se7en is already open: the running window opens them
                httpx.post(f"http://127.0.0.1:{info['port']}/api/shell/open", json={"paths": files},
                           headers={TOKEN_HEADER: info["token"]}, timeout=3)
            if sys.platform == "win32":
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(None, APP_NAME)
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 9)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                    return False
            # running in browser mode (no native window): open a new tab on it
            webbrowser.open(f"http://127.0.0.1:{info['port']}/?token={info['token']}")
            return False
    except Exception:
        pass
    return True


def _repair_url() -> str:
    """The full installer of the latest version (same repository as the automatic updates)."""
    from .updater import feed_url
    feed = feed_url() or ""
    if feed.endswith("/latest.json"):
        return feed[: -len("latest.json")] + "se7en-Censored-Setup.exe"
    return "https://github.com/se7enclaude/se7en-censored/releases/latest"


def _ensure_stdio() -> None:
    """Windowed (no console) builds have sys.stdout/stderr = None; many libraries expect streams."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    _ensure_stdio()
    try:
        return _main(argv)
    except SystemExit:
        raise
    except BaseException:
        # Never fail silently: write the traceback where the user (and support) can find it.
        import traceback
        text = traceback.format_exc()
        try:
            crash = paths().logs / "crash.log"
            crash.write_text(text, "utf-8")
        except OSError:
            crash = Path(os.environ.get("TEMP", ".")) / "se7en-crash.log"
            crash.write_text(text, "utf-8")
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            from . import preflight
            if preflight.ask(preflight.explain_crash(text, crash) + preflight.tr(
                    "\n\nOuvrir le dossier du rapport ?", "\n\nOpen the report folder?"),
                    preflight.tr("se7en n'a pas pu démarrer", "se7en could not start"), preflight.MB_ICONERROR):
                os.startfile(str(crash.parent))  # noqa: S606
        raise


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="medialab")
    ap.add_argument("--server", action="store_true", help="run the API without a window")
    ap.add_argument("--dev", action="store_true", help="API on :8765 with token 'dev' (for the Vite dev server)")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--open-browser", action="store_true")
    ap.add_argument("files", nargs="*", help="media files to open (Explorer's \"Open with\")")
    args = ap.parse_args(argv)

    # Files extracted from a downloaded zip carry the "Mark of the Web": .NET then refuses to
    # load the window's DLLs. Unblock our own program files first (Explorer's "Unblock").
    unblocked = 0
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        from . import preflight
        from .config import app_root
        unblocked = preflight.unblock_app_files(app_root())

    # A downloaded yt-dlp engine update must be activated before yt_dlp is imported anywhere.
    components.activate_engine_override()

    from .logging_setup import get_logger, setup_logging
    from .security import clear_session_file, generate_token, write_session_file
    from .settings import SettingsStore

    settings = SettingsStore().get()
    setup_logging(debug=args.debug or settings.advanced.debug_mode)
    log = get_logger("app")
    if unblocked:
        log.info("Removed the internet download mark from %d program files", unblocked)

    shell = not (args.server or args.dev)
    files = [str(Path(f).resolve()) for f in args.files if Path(f).is_file()]
    if shell and not _single_instance_or_focus(files):
        log.info("%s is already running; focused the existing window", APP_NAME)
        return 0

    port = args.port or (8765 if args.dev else (settings.advanced.api_port or _free_port()))
    token = "dev" if args.dev else generate_token()

    import uvicorn
    from .app import Services, create_app

    services = Services(token=token, shell=shell)
    services.port = port
    app = create_app(services)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                            ws_ping_interval=20, ws_ping_timeout=20, log_config=None,
                            timeout_graceful_shutdown=2)
    server = uvicorn.Server(config)
    write_session_file(port, token, shell)

    def cleanup() -> None:
        clear_session_file()
        if services.settings.get().privacy.clear_temp_on_exit:
            for d in ("img", "uploads"):
                shutil.rmtree(paths().temp / d, ignore_errors=True)

    from . import system
    system.on_quit(lambda: setattr(server, "should_exit", True) if not shell else None)

    if not shell:
        url = f"http://127.0.0.1:{port}/?token={token}"
        log.info("API listening on http://127.0.0.1:%d (token: %s)", port, "dev" if args.dev else "<session>")
        if args.open_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        try:
            server.run()
        finally:
            cleanup()
        return 0

    t = threading.Thread(target=server.run, name="api", daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)

    from .events import bus

    url = f"http://127.0.0.1:{port}/?token={token}"
    if files:
        from urllib.parse import quote
        from .media.ffmpeg import file_kind
        page = "player" if all(file_kind(f) in ("video", "audio", "image") for f in files) else "convert"
        url += f"#/{page}?" + "&".join(f"open={quote(f)}" for f in files)
    use_window = not os.environ.get("SE7EN_BROWSER")
    if use_window and sys.platform == "win32":
        from . import preflight
        wv2 = preflight.webview2_version()
        log.info("WebView2 runtime: %s · .NET 4.6.2+: %s", wv2 or "missing", preflight.dotnet_ok())
        if not wv2 or not preflight.dotnet_ok():
            use_window = False
            preflight.offer_webview2()

    try:
        if use_window:
            try:
                _run_window(url, args.debug, services, log, system, bus)
            except Exception as e:
                log.exception("The native window could not start; falling back to the web browser")
                if sys.platform == "win32" and isinstance(e, FileNotFoundError):
                    # program files missing (an update interrupted halfway), not a WebView2 problem
                    from . import preflight
                    if preflight.ask(preflight.tr(
                        "Des fichiers de se7en sont manquants (une mise à jour a été interrompue).\n\n"
                        "se7en s'ouvre dans votre navigateur en attendant.\n\n"
                        "Télécharger l'installateur pour réparer se7en ? Vos fichiers et réglages sont conservés.",
                        "Some se7en files are missing (an update was interrupted).\n\n"
                        "se7en opens in your web browser meanwhile.\n\n"
                        "Download the installer to repair se7en? Your files and settings are kept.",
                    ), preflight.tr("Réparer se7en", "Repair se7en"), preflight.MB_ICONWARNING):
                        webbrowser.open(_repair_url())
                elif sys.platform == "win32":
                    from . import preflight
                    preflight.message(preflight.tr(
                        "La fenêtre de se7en n'a pas pu s'ouvrir sur ce PC.\n\n"
                        "se7en s'ouvre dans votre navigateur à la place : tout fonctionne de la même façon.\n\n"
                        "Pour retrouver la fenêtre, installez « Microsoft Edge WebView2 Runtime ».",
                        "The se7en window could not open on this PC.\n\n"
                        "se7en opens in your web browser instead: everything works the same.\n\n"
                        "To get the window back, install \"Microsoft Edge WebView2 Runtime\".",
                    ), icon=preflight.MB_ICONWARNING)
                _run_browser(url, services, log, bus)
        else:
            _run_browser(url, services, log, bus)
    finally:
        services.jobs.shutdown()  # pause running tasks first: partial work stays resumable
        server.should_exit = True
        t.join(timeout=5)
        cleanup()
        _apply_pending_update(services, log)
    return 0


def _apply_pending_update(services, log) -> None:
    """A downloaded update is installed when se7en closes (silently, no restart)."""
    from .updater import updater
    try:
        st = updater.snapshot().get("state")
        if st == "ready" and services.settings.get().general.auto_update:
            updater.install(relaunch=False)
            log.info("Pending update installed on exit")
    except Exception as e:  # never block closing the app
        log.warning("Could not apply the pending update: %s", e)


def _run_window(url: str, debug: bool, services, log, system, bus) -> None:
    import webview
    from webview.dom import DOMEventHandler

    width, height = 1440, 900
    try:  # roomier window on big screens (the nine tabs then show their names)
        import ctypes
        sw, sh = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
        width, height = max(1100, min(1640, int(sw * 0.86))), max(720, min(1000, int(sh * 0.86)))
    except Exception:
        pass
    window = webview.create_window(
        APP_NAME, url=url, width=width, height=height,
        min_size=(1080, 700), background_color="#050505", text_select=False, zoomable=False,
    )
    system.window = window

    def on_drop(e: dict) -> None:
        files = (e.get("dataTransfer") or {}).get("files") or []
        dropped = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if dropped:
            bus.publish("files_dropped", {"paths": dropped})

    def on_loaded() -> None:
        try:
            window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=False)
        except Exception as ex:  # older WebView2 runtimes
            log.warning("Native drag & drop unavailable: %s", ex)

    system.on_quit(lambda: window.destroy())
    window.events.loaded += on_loaded
    window.events.closing += lambda: log.info("Window closing: pausing running tasks so they can resume next launch")
    storage = paths().data / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    # Let the launch animation play its sound before the first click (WebView2 reads this variable and it
    # replaces pywebview's own arguments, so they are repeated here).
    args = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    if "autoplay-policy" not in args:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = " ".join(
            filter(None, [args, "--disable-features=ElasticOverscroll", "--autoplay-policy=no-user-gesture-required"]))
    webview.start(gui="edgechromium", debug=debug, private_mode=False, storage_path=str(storage))


def _run_browser(url: str, services, log, bus) -> None:
    """Fallback UI: the default web browser. Quits once no tab is connected and nothing is running."""
    from . import system
    system.window = None
    services.shell = False  # the UI then uses the browser's drag & drop / upload path
    log.info("Running in browser mode")
    webbrowser.open(url)
    connected_once = False
    idle_since = time.monotonic()
    while True:
        if system.quit_requested.wait(2):
            log.info("Quit requested (update)")
            return
        clients = bus.subscriber_count
        busy = services.jobs.stats()["running"] > 0
        if clients or busy:
            connected_once = connected_once or bool(clients)
            idle_since = time.monotonic()
            continue
        limit = 45 if connected_once else 300
        if time.monotonic() - idle_since > limit:
            log.info("Browser tab closed and no task running: exiting")
            return

if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())

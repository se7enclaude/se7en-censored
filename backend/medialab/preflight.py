"""Windows start-up checks for the desktop build.

Problems seen when se7en is copied to another PC:

* **Mark of the Web** — files extracted from a zip received by browser / Discord /
  WhatsApp carry a ``Zone.Identifier`` stream. .NET then refuses to load the
  WebView bridge DLLs and the window cannot open. se7en removes that mark from
  *its own* files (exactly what Explorer's "Unblock" checkbox does) once the user
  has chosen to launch it.
* **WebView2 runtime missing** (some Windows 10 PCs) — pywebview would silently
  fall back to Internet Explorer, which cannot run the UI. se7en detects it,
  offers the official installer and opens the UI in the default browser meanwhile.
* **Read-only / protected folder** — handled in ``config.paths()``.
"""
from __future__ import annotations

import ctypes
import os
import sys
import webbrowser
import winreg
from pathlib import Path

WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/#download"
_WV2_KEYS = [
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # stable runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # canary
]


def is_french() -> bool:
    try:
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x0C
    except Exception:
        return False


def tr(fr: str, en: str) -> str:
    return fr if is_french() else en


# ------------------------------------------------------------------ dialogs
MB_OK, MB_YESNO, MB_ICONERROR, MB_ICONWARNING, MB_ICONINFO, IDYES = 0x0, 0x4, 0x10, 0x30, 0x40, 6


def message(text: str, title: str = "se7en", icon: int = MB_ICONINFO) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, MB_OK | icon | 0x10000)  # MB_SETFOREGROUND
    except Exception:
        pass


def ask(text: str, title: str = "se7en", icon: int = MB_ICONWARNING) -> bool:
    try:
        return ctypes.windll.user32.MessageBoxW(None, text, title, MB_YESNO | icon | 0x10000) == IDYES
    except Exception:
        return False


# ------------------------------------------------------------------ Mark of the Web
def has_web_mark(path: Path) -> bool:
    try:
        with open(f"{path}:Zone.Identifier", "rb"):
            return True
    except OSError:
        return False


def unblock_app_files(root: Path) -> int:
    """Remove the Zone.Identifier stream from se7en's own program files.

    Only runs when the executable itself carries the mark (i.e. the user
    extracted a downloaded zip and chose to launch it). Never touches files
    outside the application folder.
    """
    exe = Path(sys.executable)
    if not has_web_mark(exe):
        return 0
    count = 0
    for dirpath, dirs, files in os.walk(root):
        if Path(dirpath) == root and "data" in dirs:
            dirs.remove("data")  # portable user data (downloads, logs...) is never touched
        for name in files:
            try:
                os.remove(os.path.join(dirpath, name) + ":Zone.Identifier")
                count += 1
            except OSError:
                pass
    return count


# ------------------------------------------------------------------ WebView2
def webview2_version() -> str | None:
    """Installed Microsoft Edge WebView2 runtime version, or None."""
    locations = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{}"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{}"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{}"),
    ]
    for key in _WV2_KEYS:
        for hive, pattern in locations:
            try:
                with winreg.OpenKey(hive, pattern.format(key)) as k:
                    version, _ = winreg.QueryValueEx(k, "pv")
                    if version and version != "0.0.0.0":
                        return str(version)
            except OSError:
                continue
    return None


def dotnet_ok() -> bool:
    """.NET Framework 4.6.2+ (needed by the WebView bridge). Present on every Windows 10/11."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as k:
            release, _ = winreg.QueryValueEx(k, "Release")
            return int(release) >= 394802
    except OSError:
        return False


def offer_webview2() -> None:
    if ask(tr(
        "Le composant Microsoft Edge WebView2, qui affiche la fenêtre de se7en, n'est pas installé sur ce PC.\n\n"
        "se7en va s'ouvrir dans votre navigateur en attendant.\n\n"
        "Ouvrir la page officielle Microsoft pour l'installer maintenant (recommandé, ~2 Mo) ?",
        "The Microsoft Edge WebView2 component that displays the se7en window is not installed on this PC.\n\n"
        "se7en will open in your web browser meanwhile.\n\n"
        "Open Microsoft's official page to install it now (recommended, ~2 MB)?",
    )):
        webbrowser.open(WEBVIEW2_URL)


# ------------------------------------------------------------------ crash explanations
def explain_crash(text: str, crash_file: Path) -> str:
    low = text.lower()
    if any(k in low for k in ("0x80131515", "loadfromremotesources", "network location", "zone.identifier",
                                "failed to resolve python.runtime")):
        cause = tr(
            "Windows bloque les fichiers de se7en car ils proviennent d'internet.\n\n"
            "Solution : clic droit sur le fichier .zip › Propriétés › cocher « Débloquer » › OK, "
            "puis réextraire le zip.",
            "Windows blocks se7en's files because they were downloaded from the internet.\n\n"
            "Fix: right-click the .zip › Properties › tick \"Unblock\" › OK, then extract it again.",
        )
    elif "pythonnet" in low or "clr" in low or "webview2" in low or "corewebview2" in low:
        cause = tr(
            "La fenêtre de se7en n'a pas pu s'initialiser (composant Microsoft WebView2 ou .NET).\n\n"
            "Solution : installez « Microsoft Edge WebView2 Runtime » depuis le site de Microsoft, puis relancez se7en.",
            "The se7en window could not initialise (Microsoft WebView2 or .NET component).\n\n"
            "Fix: install \"Microsoft Edge WebView2 Runtime\" from Microsoft's website, then restart se7en.",
        )
    elif "permission" in low or "access is denied" in low or "accès refusé" in low or "winerror 5" in low:
        cause = tr(
            "se7en n'a pas le droit d'écrire dans son dossier.\n\n"
            "Solution : déplacez le dossier de se7en sur le Bureau ou dans Documents (pas dans Program Files), puis relancez.",
            "se7en is not allowed to write in its folder.\n\n"
            "Fix: move the se7en folder to the Desktop or Documents (not Program Files), then restart.",
        )
    elif "_internal" in low or "no module named" in low or "dll load failed" in low:
        cause = tr(
            "Des fichiers de se7en sont manquants (extraction incomplète ou fichiers retirés par l'antivirus).\n\n"
            "Solution : réextrayez tout le zip (clic droit › Extraire tout) et vérifiez l'historique de protection de l'antivirus.",
            "Some se7en files are missing (incomplete extraction or files removed by the antivirus).\n\n"
            "Fix: extract the whole zip again (right-click › Extract all) and check the antivirus protection history.",
        )
    else:
        cause = tr("Une erreur inattendue s'est produite au démarrage.", "An unexpected error occurred at start-up.")
    return cause + tr(f"\n\nRapport détaillé :\n{crash_file}", f"\n\nDetailed report:\n{crash_file}")

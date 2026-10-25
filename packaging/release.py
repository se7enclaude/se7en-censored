"""Release helpers used by build.ps1 / publish.ps1.

    python packaging/release.py version                 -> prints the app version
    python packaging/release.py portable                -> dist/se7en-Censored-Portable-<v>.zip (zip64, data folder excluded)
    python packaging/release.py manifest <owner/repo>   -> dist/latest.json + dist/SHA256SUMS.txt

latest.json is the update feed read by medialab/updater.py:
https://github.com/<owner/repo>/releases/latest/download/latest.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def version() -> str:
    text = (ROOT / "backend" / "medialab" / "__init__.py").read_text("utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def _zip(src: Path, out: Path, with_bin: bool) -> Path:
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for f in sorted(src.rglob("*")):
            rel = f.relative_to(src)
            if rel.parts and rel.parts[0] == "data":
                continue  # never ship someone's data
            if not with_bin and rel.parts and rel.parts[0] == "bin":
                continue  # updates keep the components already there
            if f.is_file():
                z.write(f, Path("se7en-censored") / rel)
        z.writestr("se7en-censored/portable.flag", "Delete this file to store data in %LOCALAPPDATA%\\se7enCensored instead.\n")
    print(f"{out.name}: {out.stat().st_size / 1e6:.0f} MB")
    return out


def portable() -> Path:
    """Full portable edition + the program-only zip used by portable automatic updates."""
    v = version()
    src = DIST / "se7en-censored"
    _zip(src, DIST / f"se7en-Censored-Portable-Update-{v}.zip", with_bin=False)
    return _zip(src, DIST / f"se7en-Censored-Portable-{v}.zip", with_bin=True)


def manifest(repo: str, notes_file: str | None = None) -> Path:
    v = version()
    base = f"https://github.com/{repo}/releases/download/v{v}/"
    setup = DIST / "se7en-Censored-Setup.exe"  # permanent name (hard link made by publish.ps1)
    files = {"update": DIST / f"se7en-Censored-Update-{v}.exe", "setup": setup if setup.exists() else DIST / f"se7en-Censored-Setup-{v}.exe",
             "portable": DIST / f"se7en-Censored-Portable-Update-{v}.zip", "portable_full": DIST / f"se7en-Censored-Portable-{v}.zip"}
    notes = Path(notes_file).read_text("utf-8") if notes_file and Path(notes_file).exists() else ""
    data: dict = {"version": v, "published": dt.date.today().isoformat(), "notes": notes.strip()}
    sums = []
    for key, f in files.items():
        if not f.exists():
            print(f"missing {f.name}")
            continue
        digest = sha256(f)
        data[key] = {"name": f.name, "url": base + f.name, "sha256": digest, "size": f.stat().st_size}
        sums.append(f"{digest}  {f.name}")
    out = DIST / "latest.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    (DIST / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", "utf-8")
    print(f"Manifest: {out}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "version"
    if cmd == "version":
        print(version())
    elif cmd == "portable":
        portable()
    elif cmd == "manifest":
        manifest(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)

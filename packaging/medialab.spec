# PyInstaller spec — builds dist/se7en-censored/ (one folder, two executables):
#   se7en.exe       desktop app (windowed)
#   se7en-cli.exe   console client for integrations
# Run from the repo root:  backend\.venv\Scripts\pyinstaller packaging\medialab.spec --noconfirm
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
BACKEND = ROOT / "backend"
DIST_UI = ROOT / "frontend" / "dist"
assert (DIST_UI / "index.html").exists(), "Build the UI first: cd frontend && npm run build"

hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("medialab")
    + collect_submodules("gallery_dl.extractor")
    + collect_submodules("gallery_dl.downloader")
    + collect_submodules("gallery_dl.postprocessor")
    + collect_submodules("anthropic")
    + collect_submodules("curl_cffi")
    + collect_submodules("websockets")
    + ["webview.platforms.edgechromium", "webview.platforms.winforms", "clr", "pynvml", "multipart",
       "onnxruntime", "pillow_heif"]
)
datas = [(str(DIST_UI), "frontend/dist")]
datas += collect_data_files("webview")
datas += collect_data_files("yt_dlp_ejs")
datas += collect_data_files("certifi")
binaries = collect_dynamic_libs("pillow_heif") + collect_dynamic_libs("curl_cffi")

common = dict(pathex=[str(BACKEND)], hiddenimports=hidden, datas=datas, binaries=binaries,
              excludes=["tkinter.test", "torch", "matplotlib", "spandrel", "gfpgan"])

a_app = Analysis([str(ROOT / "packaging" / "launcher.py")], **common)
a_cli = Analysis([str(ROOT / "packaging" / "cli_launcher.py")], pathex=[str(BACKEND)], hiddenimports=["medialab.cli"])

pyz_app = PYZ(a_app.pure)
pyz_cli = PYZ(a_cli.pure)

icon = str(ROOT / "packaging" / "icon.ico")
exe_app = EXE(pyz_app, a_app.scripts, [], exclude_binaries=True, name="se7en-censored",
              console=bool(os.environ.get("MEDIALAB_CONSOLE")), icon=icon,
              version=None, upx=False)
exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="se7en-censored-cli", console=True, icon=icon,
              upx=False)

coll = COLLECT(exe_app, a_app.binaries, a_app.datas, exe_cli, a_cli.binaries, a_cli.datas,
               name="se7en-censored", upx=False)

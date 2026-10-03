# PyInstaller recipe for the downloadable app, on each OS:
#   pyinstaller packaging/homestream.spec
# macOS: dist/HomeStream.app (menu bar only).  Windows: dist/HomeStream/HomeStream.exe.
# Linux: dist/HomeStream/HomeStream.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is provided by PyInstaller)

datas = [
    (str(ROOT / "homestream" / "web"), "homestream/web"),
    (str(ROOT / "config" / "env.example"), "config"),
]
binaries = []
hiddenimports = collect_submodules("homestream") + collect_submodules("uvicorn") + ["pystray"]

if sys.platform == "darwin":
    datas += collect_data_files("_sounddevice_data")
    hiddenimports += ["pystray._darwin", "AppKit", "Quartz", "ApplicationServices", "PyObjCTools.AppHelper"]
elif sys.platform == "win32":
    hiddenimports += ["pystray._win32"] + collect_submodules("winrt") + collect_submodules("soundcard")
    datas += collect_data_files("soundcard")
else:
    hiddenimports += ["pystray._xorg", "pystray._appindicator", "pystray._gtk"] + collect_submodules("soundcard")
    datas += collect_data_files("soundcard")

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "ruff"],
)
if sys.platform != "win32":
    # PortAudio ships prebuilt Windows DLLs too (pulled in by PyInstaller's own hook); drop them.
    a.datas = [d for d in a.datas if not d[0].lower().endswith(".dll")]
    a.binaries = [b for b in a.binaries if not b[0].lower().endswith(".dll")]
version_info = None
if sys.platform == "win32":
    # The name Windows shows for the app, e.g. when it asks whether HomeStream may use the network.
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )

    from homestream import __version__

    numbers = tuple(int(n) for n in __version__.split(".")) + (0,)
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("FileDescription", "HomeStream"),
                StringStruct("ProductName", "HomeStream"),
                StringStruct("CompanyName", "HomeStream"),
                StringStruct("FileVersion", __version__),
                StringStruct("ProductVersion", __version__),
                StringStruct("OriginalFilename", "HomeStream.exe"),
                StringStruct("LegalCopyright", "MIT License"),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HomeStream",
    console=False,
    icon=str(ROOT / "packaging" / ("HomeStream.ico" if sys.platform == "win32" else "HomeStream.icns")),
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, name="HomeStream")  # noqa: F821

if sys.platform == "darwin":
    from homestream import __version__

    app = BUNDLE(  # noqa: F821
        coll,
        name="HomeStream.app",
        icon=str(ROOT / "packaging" / "HomeStream.icns"),
        bundle_identifier="com.homestream.app",
        version=__version__,
        info_plist={
            "LSUIElement": True,  # menu bar only, no Dock icon
            "NSMicrophoneUsageDescription":
                "HomeStream records what this Mac plays (through BlackHole) to stream it to your phone.",
            "NSAppleEventsUsageDescription": "HomeStream can control the Spotify app from your phone.",
            "LSMinimumSystemVersion": "13.0",
        },
    )

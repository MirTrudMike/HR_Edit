# PyInstaller spec for HR_Edit tray launcher.
#
# Build command (run from the windows/ folder with the venv active):
#   pyinstaller HR_Edit.spec
#
# Output: dist/HR_Edit.exe  (~20-30 MB, standalone, no console window)

from PyInstaller.building.build_main import Analysis, PYZ, EXE

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        "pystray._win32",
        "PIL._imaging",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HR_Edit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no black terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="hr_edit.ico",   # uncomment and add .ico file to use custom icon
)

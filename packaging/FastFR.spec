from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


root = Path(SPECPATH).parent
datas = [(str(root / "dist"), "dist"), (str(root / "models"), "models")]
datas += collect_data_files("onnxruntime")
binaries = collect_dynamic_libs("onnxruntime")
hiddenimports = [
    "fastfr.main",
    "onnxruntime.capi._pybind_state",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

analysis = Analysis(
    [str(root / "packaging" / "fastfr_entry.py")],
    pathex=[str(root / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "tkinter",
        "matplotlib",
        "notebook",
        "pandas",
        "scipy",
        "onnxruntime.quantization",
        "onnxruntime.tools",
        "onnxruntime.transformers",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="FastFR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="FastFR",
)

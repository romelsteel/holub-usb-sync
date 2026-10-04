# -*- coding: utf-8 -*-
"""Postaví holub.exe (PyInstaller) a z něj instalátor (Inno Setup).

Použití:  py build_installer.py [verze]
Výsledek: dist-installer/Holub-Setup-<verze>.exe
"""
import os
import shutil
import subprocess
import sys

SLOZKA = os.path.dirname(os.path.abspath(__file__))
VERZE = sys.argv[1] if len(sys.argv) > 1 else None  # jinak holub.VERZE
ISCC_CESTY = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def main():
    os.chdir(SLOZKA)
    sys.path.insert(0, SLOZKA)
    import holub  # pixelová mapa holuba → ikona; a číslo verze
    global VERZE
    VERZE = VERZE or holub.VERZE
    ico = os.path.join("installer", "holub.ico")
    holub.vykresli_ikonu(holub.MAPA_STOJICI, holub.PALETA, 256).save(
        ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])

    for slozka in ("build", "dist"):
        shutil.rmtree(slozka, ignore_errors=True)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed",
         "--name", "Holub", "--icon", ico, "holub.py"], check=True)

    iscc = next((c for c in ISCC_CESTY if os.path.isfile(c)), None)
    if not iscc:
        sys.exit("Inno Setup 6 nenalezen (ISCC.exe).")
    subprocess.run([iscc, os.path.join("installer", "holub.iss")], check=True,
                   env=dict(os.environ, HOLUB_VERSION=VERZE))
    print(f"\nHotovo: dist-installer/Holub-Setup-{VERZE}.exe")


if __name__ == "__main__":
    main()

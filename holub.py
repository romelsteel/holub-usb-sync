# -*- coding: utf-8 -*-
"""Holub — synchronizace Obsidian vaultu na USB disk.

Malá appka v oznamovací oblasti Windows (ikona u hodin). Hlídá připojení
spárovaného USB disku (pozná ho podle skrytého souboru .holub-usb v kořeni
disku) a synchronizuje na něj Obsidian vault. Celý návrh je v plan.md.

Spuštění bez černého okna:  pythonw holub.py
"""

import ctypes
import json
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

import pystray
from PIL import Image
from winotify import Notification

# ---------------------------------------------------------------------------
# Cesty a konstanty
# ---------------------------------------------------------------------------

ZABALENO = getattr(sys, "frozen", False)  # běží jako holub.exe (instalátor)?
SLOZKA = os.path.dirname(sys.executable if ZABALENO else os.path.abspath(__file__))
# data appky: ze zdrojáku vedle skriptu, z instalace v %APPDATA%\Holub
SLOZKA_DAT = (os.path.join(os.environ.get("APPDATA", SLOZKA), "Holub")
              if ZABALENO else SLOZKA)
os.makedirs(SLOZKA_DAT, exist_ok=True)
CESTA_CONFIG = os.path.join(SLOZKA_DAT, "config.json")
if "--config" in sys.argv:  # jiný config pro testování na cvičném vaultu
    CESTA_CONFIG = sys.argv[sys.argv.index("--config") + 1]

CESTA_LOG = os.path.join(SLOZKA_DAT, "holub.log")
CESTA_TOAST_IKONY = os.path.join(SLOZKA_DAT, "holub-ikona.png")
CESTA_HISTORIE = os.path.join(SLOZKA_DAT, "holub-historie.json")
CESTA_AUTOSTART = os.path.join(
    os.environ.get("APPDATA", ""), "Microsoft", "Windows",
    "Start Menu", "Programs", "Startup", "Holub.pyw")

ZNACKA_USB = ".holub-usb"              # párovací soubor v kořeni USB disku
SLOZKA_ZALOHY = "Vault"                # složka s kopií vaultu na USB
SOUBOR_SNIMKU = "holub-snapshot.json"  # paměť posledního syncu (obousměrný režim)
TOLERANCE_CASU = 3                     # s — FAT32 ukládá časy jen po 2 s

KOS_SLOZKA = ".holub-kos"              # smazané se nemažou, stěhují se sem
KOS_DNY = 30                           # jak dlouho koš drží smazané soubory
POJISTKA_MIN_SOUBORU = 5               # pojistka mazání: méně souborů neřeší…
POJISTKA_PODIL = 0.2                   # …víc než 20 % poznámek už ano
CESTA_PREHLED_PYW = os.path.join(
    SLOZKA_DAT, "otevri-prehled.vbs" if ZABALENO else "otevri-prehled.pyw")
AUTOSTART_KLIC = r"Software\Microsoft\Windows\CurrentVersion\Run"

VYCHOZI_CONFIG = {
    "vault": "",
    "rezim": "jednosmerny",            # "jednosmerny" | "obousmerny"
    "interval_kontroly_s": 5,
    "ignorovat": [".obsidian/workspace.json", ".obsidian/workspace-mobile.json"],
    "posledni_sync": "",
    "casovac": "vypnuto",              # "vypnuto" | "interval" | "denne"
    "casovac_minuty": 30,              # pro režim "interval"
    "casovac_denne": "18:00",          # pro režim "denne" (HH:MM)
}

CFG = dict(VYCHOZI_CONFIG)

# Sdílený stav appky. "stav" je jedno z: ceka / ok / sync / chyba / hotovo
# "usb" je seznam všech připojených spárovaných disků (např. ["E:\\", "F:\\"])
S = {"stav": "ceka", "usb": [], "bezi": True, "chyba_text": "",
     "vault_chybi": False, "casovac_pokus": 0.0, "casovac_den": "",
     "povolit_mazani": False, "mazani_ceka": False}
IKONA = {"obj": None}          # pystray ikona (naplní se v main)
ZAMEK_SYNCU = threading.Lock()  # sync nikdy nesmí běžet dvakrát naráz

# ---------------------------------------------------------------------------
# Pixel-art ikony (mapy 16×16 z plan.md, "." = průhledná)
# ---------------------------------------------------------------------------

PALETA = {
    "B": (0x8F, 0xA3, 0xBF, 255),  # tělo
    "D": (0x5F, 0x73, 0x96, 255),  # tmavá (křídlo/ocas)
    "W": (0xCD, 0xD9, 0xE8, 255),  # světlá skvrna
    "K": (0xE5, 0x9A, 0x3C, 255),  # zobák
    "E": (0x23, 0x27, 0x2E, 255),  # oko
    "G": (0x3F, 0xAE, 0x7A, 255),  # zelený lesk krku
    "L": (0xD9, 0x6A, 0x45, 255),  # nohy
    "N": (0xF7, 0xF2, 0xE2, 255),  # poznámka v zobáku
    "R": (0xD9, 0x4F, 0x4F, 255),  # červený odznak chyby
    "X": (0xFF, 0xFF, 0xFF, 255),  # bílá
    "Z": (0x35, 0xC2, 0x6B, 255),  # zelený odznak hotovo
}

# Šedá varianta pro stav "čeká na USB" (spící holub)
PALETA_SEDA = dict(PALETA, **{
    "B": (0xA6, 0xA6, 0xA6, 255), "D": (0x8B, 0x8B, 0x8B, 255),
    "W": (0xC8, 0xC8, 0xC8, 255), "K": (0xB3, 0xB3, 0xB3, 255),
    "E": (0x7A, 0x7A, 0x7A, 255), "G": (0x9C, 0x9C, 0x9C, 255),
    "L": (0x9E, 0x9E, 0x9E, 255),
})

MAPA_STOJICI = """
................
....BB..........
...BBBB.........
..KBEBB.........
...BBBB.........
....GGBB........
....GBBBBB......
....BBBBBBBB....
....BWWWWBBBDD..
....BWWWWWBBBDD.
.....BWWWWBBDD..
......BBBBBB....
.......L..L.....
......LL..LL....
................
................
"""

MAPA_LET_1 = """
........DDD.....
.......DDDD.....
.......DDD......
....BB.DDD......
...BBBB.DD......
..KBEBBBBBBBDD..
.NNBBBBBBBBBDDD.
.NN.BBWWWWBBBDD.
.....BBBBBBB....
......BBBB......
"""

MAPA_LET_2 = """
................
................
................
....BB..........
...BBBB.........
..KBEBBBBBBBDD..
.NNBBBBBBBBBDDD.
.NN.BBDDDDBBBDD.
.....DDDDDDB....
......DDDD......
.......DD.......
"""

MAPA_KLOVNUTI = """
................
................
....BB..........
...BBBB.........
..KBEBB.........
...BBBB.........
....GGBB........
....BBBBBBBB....
....BWWWWBBBDD..
....BWWWWWBBBDD.
.....BWWWWBBDD..
......BBBBBB....
.......L..L.....
......LL..LL....
................
................
"""

MAPA_CHYBA = """
............RRR.
....BB.....RRXRR
...BBBB....RRXRR
..KBEBB....RRRRR
...BBBB.....RXR.
....GGBB........
....GBBBBB......
....BBBBBBBB....
....BWWWWBBBDD..
....BWWWWWBBBDD.
.....BWWWWBBDD..
......BBBBBB....
.......L..L.....
......LL..LL....
................
................
"""

MAPA_HOTOVO = """
............ZZZ.
....BB.....ZZZZX
...BBBB....XZZXZ
..KBEBB....ZXXZZ
...BBBB.....ZZZ.
....GGBB........
....GBBBBB......
....BBBBBBBB....
....BWWWWBBBDD..
....BWWWWWBBBDD.
.....BWWWWBBDD..
......BBBBBB....
.......L..L.....
......LL..LL....
................
................
"""


def vykresli_ikonu(mapa, paleta, velikost=64):
    """Z textové pixelové mapy udělá obrázek (zvětšený beze ztráty ostrosti)."""
    obrazek = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for y, radek in enumerate(mapa.strip("\n").split("\n")):
        for x, znak in enumerate(radek):
            if znak != ".":
                obrazek.putpixel((x, y), paleta[znak])
    return obrazek.resize((velikost, velikost), Image.NEAREST)


IKONY = {
    "ceka": vykresli_ikonu(MAPA_STOJICI, PALETA_SEDA),
    "ok": vykresli_ikonu(MAPA_STOJICI, PALETA),
    "let1": vykresli_ikonu(MAPA_LET_1, PALETA),
    "let2": vykresli_ikonu(MAPA_LET_2, PALETA),
    "chyba": vykresli_ikonu(MAPA_CHYBA, PALETA),
    "hotovo": vykresli_ikonu(MAPA_HOTOVO, PALETA),
    # klidové drobnosti: mrknutí (oko splyne s tělem) a klovnutí
    "mrk": vykresli_ikonu(MAPA_STOJICI, dict(PALETA, E=PALETA["B"])),
    "klov": vykresli_ikonu(MAPA_KLOVNUTI, PALETA),
}

# ---------------------------------------------------------------------------
# Drobné pomůcky
# ---------------------------------------------------------------------------


def loguj(text):
    try:
        with open(CESTA_LOG, "a", encoding="utf-8") as soubor:
            soubor.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        pass


def zajisti_prehled_pyw():
    """Vyrobí skript, který z tlačítka na oznámení otevře okno Přehledu.

    Kliknutí na tlačítko spustí tenhle skriptík: ten jen „zazvoní" na běžícího
    Holuba přes pojmenovanou událost Windows (a když Holub neběží, spustí ho)."""
    if ZABALENO:  # holub.exe --ukaz-prehled; .vbs ho spustí bez černého okna
        try:
            with open(CESTA_PREHLED_PYW, "w", encoding="utf-8") as soubor:
                soubor.write('CreateObject("WScript.Shell").Run """%s"" --ukaz-prehled", 0, False\r\n'
                             % sys.executable)
        except OSError:
            loguj(traceback.format_exc())
    elif not os.path.isfile(CESTA_PREHLED_PYW):
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.isfile(pythonw):
            pythonw = sys.executable
        skript = os.path.join(SLOZKA, "holub.py")
        obsah = (
            "import ctypes, subprocess\n"
            "kernel32 = ctypes.windll.kernel32\n"
            'udalost = kernel32.CreateEventW(None, False, False, "Holub-ukaz-prehled")\n'
            "kernel32.SetEvent(udalost)\n"
            'mutex = kernel32.OpenMutexW(0x100000, False, "Holub-USB-sync")\n'
            "if not mutex:\n"
            f'    subprocess.Popen([r"{pythonw}", r"{skript}"], cwd=r"{SLOZKA}")\n'
            "else:\n"
            "    kernel32.CloseHandle(mutex)\n"
        )
        try:
            with open(CESTA_PREHLED_PYW, "w", encoding="utf-8") as soubor:
                soubor.write(obsah)
        except OSError:
            loguj(traceback.format_exc())
    return CESTA_PREHLED_PYW


def toast(titulek, text="", prehled=False):
    try:
        oznameni = Notification(app_id="Holub", title=titulek, msg=text,
                                icon=CESTA_TOAST_IKONY)
        if prehled:
            oznameni.add_actions("Otevřít přehled", zajisti_prehled_pyw())
        oznameni.show()
    except Exception:
        loguj("Toast selhal:\n" + traceback.format_exc())


def zajisti_toast_ikonu():
    """Toasty potřebují ikonu jako soubor na disku — vyrobí se z pixelové mapy."""
    if not os.path.isfile(CESTA_TOAST_IKONY):
        try:
            vykresli_ikonu(MAPA_STOJICI, PALETA, 96).save(CESTA_TOAST_IKONY)
        except OSError:
            loguj("Nepodařilo se uložit ikonu pro oznámení.")


def nacti_config():
    try:
        with open(CESTA_CONFIG, encoding="utf-8") as soubor:
            CFG.update(json.load(soubor))
    except (OSError, ValueError):
        pass  # první spuštění (nebo poškozený config) → výchozí hodnoty


def uloz_config():
    try:
        with open(CESTA_CONFIG, "w", encoding="utf-8") as soubor:
            json.dump(CFG, soubor, ensure_ascii=False, indent=2)
    except OSError:
        loguj("Nepodařilo se uložit config:\n" + traceback.format_exc())


def nacti_historii():
    try:
        with open(CESTA_HISTORIE, encoding="utf-8") as soubor:
            return json.load(soubor)
    except (OSError, ValueError):
        return []


def zapis_historii(zprava, chyba=False):
    """Přidá záznam do historie synchronizací (drží se posledních 200)."""
    zaznamy = nacti_historii()
    zaznamy.append({"kdy": datetime.now().isoformat(timespec="seconds"),
                    "rezim": CFG.get("rezim"), "zprava": zprava, "chyba": chyba})
    try:
        with open(CESTA_HISTORIE, "w", encoding="utf-8") as soubor:
            json.dump(zaznamy[-200:], soubor, ensure_ascii=False, indent=1)
    except OSError:
        loguj("Nepodařilo se zapsat historii:\n" + traceback.format_exc())


def popis_poctu(pocet, tvary):
    """České skloňování: tvary = (1 kus, 2–4 kusy, 5+ kusů)."""
    if pocet == 1:
        tvar = tvary[0]
    elif 2 <= pocet <= 4:
        tvar = tvary[1]
    else:
        tvar = tvary[2]
    return f"{pocet} {tvar}"


def hezky_cas(iso):
    try:
        kdy = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if kdy.date() == datetime.now().date():
        return f"dnes {kdy:%H:%M}"
    return f"{kdy.day}. {kdy.month}. {kdy:%H:%M}"


def dialog_slozka(titulek):
    """Windows dialog pro výběr složky. Běží v pomocném procesu, aby se
    tkinter nepral s vlákny pystray."""
    vystup = os.path.join(SLOZKA_DAT, "dialog-vysledek.txt")
    try:
        os.remove(vystup)
    except OSError:
        pass
    prikaz = ([sys.executable] if ZABALENO
              else [sys.executable, os.path.abspath(__file__)])
    subprocess.run(prikaz + ["--dialog-slozka", titulek, vystup],
                   creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        with open(vystup, encoding="utf-8") as soubor:
            cesta = soubor.read().strip()
    except OSError:
        cesta = ""
    return os.path.normpath(cesta) if cesta else None


def dialog_slozka_proces(titulek, vystup):
    """Pomocný proces: ukáže dialog a výsledek zapíše do souboru."""
    import tkinter as tk
    from tkinter import filedialog
    okno = tk.Tk()
    okno.withdraw()
    okno.attributes("-topmost", True)
    cesta = filedialog.askdirectory(title=titulek) or ""
    with open(vystup, "w", encoding="utf-8") as soubor:
        soubor.write(cesta)

# ---------------------------------------------------------------------------
# Detekce USB disku
# ---------------------------------------------------------------------------


def pripojene_disky():
    maska = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{chr(65 + i)}:\\" for i in range(26) if maska >> i & 1]


def najdi_usb_vsechny():
    """Vrátí kořeny všech připojených disků se značkou .holub-usb."""
    system = (os.environ.get("SystemDrive", "C:") + "\\").upper()
    nalezene = []
    for disk in pripojene_disky():
        if disk.upper() == system:
            continue
        try:
            if os.path.isfile(os.path.join(disk, ZNACKA_USB)):
                nalezene.append(disk)
        except OSError:
            pass
    return nalezene

# ---------------------------------------------------------------------------
# Synchronizace — jádro appky
#
# Bezpečnostní pravidla z plan.md:
#   1. Jednosměrný režim NIKDY nezapisuje ani nemaže na PC.
#   2. Obousměrný režim NIKDY potichu nepřepisuje konflikt.
#   3. Nikdy nesyncovat bez platné párovací značky na disku.
# ---------------------------------------------------------------------------


class MnohoMazani(Exception):
    """Pojistka: synchronizace by smazala podezřele velkou část poznámek."""

    def __init__(self, kde, kolik, celkem):
        super().__init__(f"chce smazat {kolik} z {celkem} poznámek {kde}")
        self.kde = kde
        self.kolik = kolik
        self.celkem = celkem


def zkontroluj_pojistku(kolik, celkem, kde, povoleno):
    if povoleno or kolik < POJISTKA_MIN_SOUBORU:
        return
    if kolik > celkem * POJISTKA_PODIL:
        raise MnohoMazani(kde, kolik, celkem)


def presun_do_kose(kos, koren, rel):
    """Místo smazání se soubor přestěhuje do koše (podsložka podle data)."""
    cil = os.path.join(kos, datetime.now().date().isoformat(), rel)
    os.makedirs(os.path.dirname(cil), exist_ok=True)
    try:
        ctypes.windll.kernel32.SetFileAttributesW(kos, 2)  # skrytá složka
    except Exception:
        pass
    if os.path.exists(cil):
        os.remove(cil)
    shutil.move(os.path.join(koren, rel), cil)


def vycisti_kos(kos):
    """Vysype z koše podsložky starší než KOS_DNY dní."""
    if not os.path.isdir(kos):
        return
    for jmeno in os.listdir(kos):
        try:
            stari = (datetime.now() - datetime.fromisoformat(jmeno)).days
        except ValueError:
            continue  # cizí složka — nesahat
        if stari > KOS_DNY:
            shutil.rmtree(os.path.join(kos, jmeno), ignore_errors=True)


def projdi(koren, ignorovat):
    """Vrátí {relativní cesta: (čas úpravy, velikost)} všech souborů ve složce."""
    soubory = {}
    for cesta, _slozky, jmena in os.walk(koren):
        for jmeno in jmena:
            plna = os.path.join(cesta, jmeno)
            rel = os.path.relpath(plna, koren).replace("\\", "/")
            if rel in ignorovat or rel.startswith(KOS_SLOZKA + "/"):
                continue
            try:
                udaje = os.stat(plna)
            except OSError:
                continue  # soubor mezitím zmizel
            soubory[rel] = (udaje.st_mtime, udaje.st_size)
    return soubory


def stejne(a, b):
    """Srovnání podle velikosti a času úpravy (s tolerancí kvůli FAT32)."""
    return a[1] == b[1] and abs(a[0] - b[0]) <= TOLERANCE_CASU


def zkopiruj(odkud, kam, rel):
    cil = os.path.join(kam, rel)
    os.makedirs(os.path.dirname(cil) or kam, exist_ok=True)
    shutil.copy2(os.path.join(odkud, rel), cil)


def smaz_prazdne_slozky(koren):
    for cesta, _slozky, _jmena in os.walk(koren, topdown=False):
        if os.path.normpath(cesta) == os.path.normpath(koren):
            continue
        try:
            os.rmdir(cesta)  # selže, když složka není prázdná — to je v pořádku
        except OSError:
            pass


def sync_jednosmerny(vault, cil, ignorovat, naostro=True, povolit_mazani=False):
    """Zrcadlo PC → USB. Na PC nesahá — jen čte.

    S naostro=False jen spočítá, co by se stalo, a ničeho se nedotkne.
    „Smazání" na USB je přesun do koše .holub-kos (drží se KOS_DNY dní)."""
    na_pc = projdi(vault, ignorovat)
    na_usb = projdi(cil, ignorovat)
    kopirovat = [rel for rel, udaje in na_pc.items()
                 if rel not in na_usb or not stejne(udaje, na_usb[rel])]
    smazat = [rel for rel in na_usb if rel not in na_pc]

    if not naostro:
        return len(kopirovat), len(smazat)

    zkontroluj_pojistku(len(smazat), len(na_usb), "na USB", povolit_mazani)
    os.makedirs(cil, exist_ok=True)
    for rel in kopirovat:
        zkopiruj(vault, cil, rel)
    kos = os.path.join(os.path.dirname(cil), KOS_SLOZKA)
    for rel in smazat:
        presun_do_kose(kos, cil, rel)
    if smazat:
        smaz_prazdne_slozky(cil)
    return len(kopirovat), len(smazat)


def konfliktni_jmeno(rel, vault, cil):
    """Volné jméno pro konfliktní kopii, např. 'poznamka (konflikt z USB).md'."""
    kmen, pripona = os.path.splitext(rel)
    cislo = 0
    while True:
        priznak = " (konflikt z USB)" if cislo == 0 else f" (konflikt z USB {cislo + 1})"
        kandidat = kmen + priznak + pripona
        if (not os.path.exists(os.path.join(vault, kandidat))
                and not os.path.exists(os.path.join(cil, kandidat))):
            return kandidat
        cislo += 1


def sync_obousmerny(vault, cil, cesta_snimku, ignorovat, naostro=True,
                    povolit_mazani=False):
    """PC ⇄ USB. Snímek posledního syncu rozlišuje „smazáno" od „nové".

    Nejdřív se sestaví plán akcí, pak se teprve provádí — díky tomu umí
    pojistka zastavit podezřele velké mazání dřív, než se čehokoli dotkne.
    S naostro=False vrátí jen počty z plánu. „Smazání" = přesun do koše."""
    na_pc = projdi(vault, ignorovat)
    na_usb = projdi(cil, ignorovat)

    snimek = {}
    if os.path.isfile(cesta_snimku):
        try:
            with open(cesta_snimku, encoding="utf-8") as soubor:
                snimek = {rel: tuple(udaje) for rel, udaje in json.load(soubor).items()}
        except (OSError, ValueError):
            snimek = {}  # poškozený snímek → nic se nemaže, jen se slévá obsah

    plan = {"na_usb": [], "na_pc": [], "smazat_pc": [], "smazat_usb": [],
            "konflikty": []}

    for rel in sorted(set(na_pc) | set(na_usb)):
        je_pc, je_usb = rel in na_pc, rel in na_usb
        zmena_pc = je_pc and (rel not in snimek or not stejne(na_pc[rel], snimek[rel]))
        zmena_usb = je_usb and (rel not in snimek or not stejne(na_usb[rel], snimek[rel]))

        if je_pc and je_usb:
            if stejne(na_pc[rel], na_usb[rel]):
                continue
            if zmena_pc and zmena_usb:
                plan["konflikty"].append(rel)
            elif zmena_pc:
                plan["na_usb"].append(rel)
            elif zmena_usb:
                plan["na_pc"].append(rel)
            elif na_pc[rel][0] >= na_usb[rel][0]:
                # hraniční případ (snímek sedí na obě strany, přesto se liší):
                # vyhrává novější verze
                plan["na_usb"].append(rel)
            else:
                plan["na_pc"].append(rel)
        elif je_pc:
            if rel in snimek and not zmena_pc:
                plan["smazat_pc"].append(rel)     # smazáno na USB
            else:
                plan["na_usb"].append(rel)        # nové na PC (úprava poráží smazání)
        else:
            if rel in snimek and not zmena_usb:
                plan["smazat_usb"].append(rel)    # smazáno na PC
            else:
                plan["na_pc"].append(rel)         # nové na USB

    vysledek = {"na_usb": len(plan["na_usb"]), "na_pc": len(plan["na_pc"]),
                "smazano_usb": len(plan["smazat_usb"]),
                "smazano_pc": len(plan["smazat_pc"]),
                "konflikty": len(plan["konflikty"])}
    if not naostro:
        return vysledek

    zkontroluj_pojistku(len(plan["smazat_usb"]), len(na_usb), "na USB",
                        povolit_mazani)
    zkontroluj_pojistku(len(plan["smazat_pc"]), len(na_pc), "na PC",
                        povolit_mazani)

    os.makedirs(cil, exist_ok=True)
    for rel in plan["na_usb"]:
        zkopiruj(vault, cil, rel)
    for rel in plan["na_pc"]:
        zkopiruj(cil, vault, rel)
    for rel in plan["konflikty"]:
        # Konflikt: verze z PC si nechá původní jméno, verze z USB se uloží
        # vedle ní — na obou stranách, nic se neztratí.
        kopie = konfliktni_jmeno(rel, vault, cil)
        shutil.copy2(os.path.join(cil, rel), os.path.join(cil, kopie))
        shutil.copy2(os.path.join(cil, rel), os.path.join(vault, kopie))
        zkopiruj(vault, cil, rel)
    kos_usb = os.path.join(os.path.dirname(cil), KOS_SLOZKA)
    kos_pc = os.path.join(vault, KOS_SLOZKA)
    for rel in plan["smazat_usb"]:
        presun_do_kose(kos_usb, cil, rel)
    for rel in plan["smazat_pc"]:
        presun_do_kose(kos_pc, vault, rel)

    if plan["smazat_usb"]:
        smaz_prazdne_slozky(cil)
    if plan["smazat_pc"]:
        smaz_prazdne_slozky(vault)

    novy_snimek = projdi(vault, ignorovat)
    with open(cesta_snimku, "w", encoding="utf-8") as soubor:
        json.dump(novy_snimek, soubor)
    return vysledek

# ---------------------------------------------------------------------------
# Stav ikony a spouštění synchronizace
# ---------------------------------------------------------------------------


def nastav_stav(novy, chyba_text=""):
    S["stav"] = novy
    S["chyba_text"] = chyba_text
    ikona = IKONA["obj"]
    if ikona is not None:
        try:
            ikona.icon = IKONY["let1" if novy == "sync" else novy]
            ikona.title = "Holub · " + stavovy_text()
            ikona.update_menu()
        except Exception:
            loguj("Nepodařilo se překreslit ikonu:\n" + traceback.format_exc())


def stavovy_text(_item=None):
    if not CFG.get("vault"):
        return "nejdřív zvol složku vaultu"
    if S["stav"] == "sync":
        return "synchronizuji…"
    if S["stav"] == "chyba":
        return S["chyba_text"] or "chyba synchronizace"
    if not S["usb"]:
        return "čekám na USB disk"
    disky = f" · {len(S['usb'])} disky" if len(S["usb"]) > 1 else ""
    if CFG.get("posledni_sync"):
        return "vše synchronizováno · " + hezky_cas(CFG["posledni_sync"]) + disky
    return "USB připojené · zatím nesynchronizováno"


def formatuj_cas(sekundy):
    if sekundy < 10:
        return f"{sekundy:.1f} s".replace(".", ",")
    return f"{round(sekundy)} s"


def zprava_jednosmerna(zkopirovano, smazano, trvani=None):
    casti = []
    if zkopirovano:
        casti.append(popis_poctu(zkopirovano, (
            "poznámka zkopírována na USB",
            "poznámky zkopírovány na USB",
            "poznámek zkopírováno na USB")))
    if smazano:
        casti.append(popis_poctu(smazano, (
            "smazána na USB", "smazány na USB", "smazáno na USB")))
    if not casti:
        casti.append("vše už bylo aktuální")
    if trvani is not None:
        casti.append(formatuj_cas(trvani))
    return " · ".join(casti)


def zprava_obousmerna(vysledek, trvani=None):
    casti = []
    if vysledek["na_usb"]:
        casti.append(f"{vysledek['na_usb']} → USB")
    if vysledek["na_pc"]:
        casti.append(f"{vysledek['na_pc']} → PC")
    smazano = vysledek["smazano_usb"] + vysledek["smazano_pc"]
    if smazano:
        casti.append(f"smazáno {smazano}")
    if vysledek["konflikty"]:
        casti.append(popis_poctu(vysledek["konflikty"],
                                 ("konflikt", "konflikty", "konfliktů")))
    if not casti:
        casti.append("vše už bylo aktuální")
    if trvani is not None:
        casti.append(formatuj_cas(trvani))
    return " · ".join(casti)


def synchronizuj():
    """Jeden běh synchronizace všech připojených spárovaných disků.
    Volat vždy v samostatném vlákně."""
    if not ZAMEK_SYNCU.acquire(blocking=False):
        return  # už běží
    disk_prave = None
    try:
        vault = CFG.get("vault")
        if not vault or not os.path.isdir(vault):
            nastav_stav("chyba", "nenacházím složku vaultu")
            toast("Nenacházím složku vaultu",
                  "V menu zvol „Zvolit složku vaultu…“ a vyber ji znovu.")
            return
        disky = list(S["usb"]) or najdi_usb_vsechny()
        if not disky:
            toast("USB disk není připojený",
                  "Zasuň spárovaný USB disk, synchronizace se spustí sama.")
            return
        povolit_mazani = S["povolit_mazani"]
        S["povolit_mazani"] = False  # potvrzení platí jen pro jeden běh

        nastav_stav("sync")
        zacatek = time.time()
        ignorovat = set(CFG.get("ignorovat", []))
        casti = []
        konflikty_celkem = 0

        for disk in disky:
            disk_prave = disk
            cil = os.path.join(disk, SLOZKA_ZALOHY)
            if CFG.get("rezim") == "obousmerny":
                vysledek = sync_obousmerny(
                    vault, cil, os.path.join(disk, SOUBOR_SNIMKU), ignorovat,
                    povolit_mazani=povolit_mazani)
                cast = zprava_obousmerna(vysledek)
                konflikty_celkem += vysledek["konflikty"]
            else:
                zkopirovano, smazano = sync_jednosmerny(
                    vault, cil, ignorovat, povolit_mazani=povolit_mazani)
                cast = zprava_jednosmerna(zkopirovano, smazano)
            casti.append(cast if len(disky) == 1 else f"{disk[0]}: {cast}")
            vycisti_kos(os.path.join(disk, KOS_SLOZKA))
        vycisti_kos(os.path.join(vault, KOS_SLOZKA))

        zprava = " | ".join(casti) + " · " + formatuj_cas(time.time() - zacatek)
        if konflikty_celkem:
            toast("Pozor, konflikt poznámek",
                  "Stejná poznámka byla změněná na PC i na USB. Obě verze "
                  "jsou uložené — hledej soubory „(konflikt z USB)“.",
                  prehled=True)

        CFG["posledni_sync"] = datetime.now().isoformat(timespec="seconds")
        uloz_config()
        zapis_historii(zprava)
        nastav_stav("hotovo")
        toast("Synchronizace dokončena", zprava, prehled=True)
        time.sleep(1.5)  # zelená fajfka chvíli svítí…
        if S["stav"] == "hotovo":
            nastav_stav("ok")  # …a pak zpět na „vše v pořádku"
    except MnohoMazani as pojistka:
        # bezpečnostní brzda — nic se nesmazalo, čeká se na rozhodnutí
        S["mazani_ceka"] = True
        POTVRZENI["info"] = {"kde": pojistka.kde, "kolik": pojistka.kolik,
                             "celkem": pojistka.celkem, "disk": disk_prave}
        kratce = f"pozastaveno — chtělo se smazat {pojistka.kolik} poznámek"
        zapis_historii(kratce, chyba=True)
        nastav_stav("chyba", kratce)
        toast("Synchronizace pozastavena",
              f"Chystala se smazat {pojistka.kolik} z {pojistka.celkem} "
              f"poznámek {pojistka.kde} — to je podezřele moc, tak jsem se "
              "raději zastavil.", prehled=True)
        akce_potvrzeni_mazani()
    except OSError as chyba:
        loguj(traceback.format_exc())
        if getattr(chyba, "errno", None) == 28:
            text = "Na USB disku není dost místa. Uvolni místo a zkus to znovu."
            kratce = "USB disk je plný"
        elif disk_prave and not os.path.isdir(disk_prave):
            text = ("USB disk byl odpojen uprostřed synchronizace. Zasuň ho "
                    "znovu — synchronizace se spustí sama a dokončí se.")
            kratce = "USB odpojeno při synchronizaci"
        else:
            text = f"{chyba.strerror or chyba}. Zkus synchronizaci spustit znovu."
            kratce = "chyba při synchronizaci"
        zapis_historii(kratce, chyba=True)
        nastav_stav("chyba", kratce)
        toast("Synchronizace selhala", text, prehled=True)
    except Exception:
        loguj(traceback.format_exc())
        zapis_historii("neočekávaná chyba — viz holub.log", chyba=True)
        nastav_stav("chyba", "neočekávaná chyba — viz holub.log")
        toast("Synchronizace selhala",
              "Neočekávaná chyba, podrobnosti jsou v souboru holub.log.")
    finally:
        ZAMEK_SYNCU.release()

# ---------------------------------------------------------------------------
# Akce z menu
# ---------------------------------------------------------------------------


def akce_sync(_ikona=None, _polozka=None):
    threading.Thread(target=synchronizuj, daemon=True).start()


def nastav_rezim(rezim):
    CFG["rezim"] = rezim
    uloz_config()
    if IKONA["obj"]:
        IKONA["obj"].update_menu()


def akce_otevrit_zalohu(_ikona=None, _polozka=None):
    disky = list(S["usb"]) or najdi_usb_vsechny()
    for disk in disky:
        if os.path.isdir(os.path.join(disk, SLOZKA_ZALOHY)):
            os.startfile(os.path.join(disk, SLOZKA_ZALOHY))
            return
    if disky:
        toast("Záloha zatím neexistuje", "Nejdřív spusť synchronizaci.")
    else:
        toast("USB disk není připojený", "Zasuň spárovaný USB disk.")


def akce_zvolit_vault(_ikona=None, _polozka=None):
    threading.Thread(target=_zvolit_vault, daemon=True).start()


def _zvolit_vault():
    cesta = dialog_slozka("Vyber složku Obsidian vaultu")
    if not cesta:
        return
    CFG["vault"] = cesta
    uloz_config()
    S["vault_chybi"] = False
    nastav_stav("ok" if S["usb"] else "ceka")
    toast("Vault nastaven", f"Budu zálohovat: {cesta}")
    if S["usb"]:
        akce_sync()


def akce_parovat(_ikona=None, _polozka=None):
    threading.Thread(target=_parovat, daemon=True).start()


def _parovat():
    cesta = dialog_slozka("Vyber USB disk, který chceš spárovat (např. E:\\)")
    if not cesta:
        return
    koren = os.path.splitdrive(os.path.abspath(cesta))[0] + "\\"
    system = (os.environ.get("SystemDrive", "C:") + "\\").upper()
    if koren.upper() == system:
        toast("Tohle je systémový disk",
              "Vyber prosím USB disk, ne disk s Windows.")
        return
    znacka = os.path.join(koren, ZNACKA_USB)
    try:
        with open(znacka, "w", encoding="utf-8") as soubor:
            json.dump({"aplikace": "Holub",
                       "sparovano": datetime.now().isoformat(timespec="seconds")},
                      soubor)
        ctypes.windll.kernel32.SetFileAttributesW(znacka, 2)  # skrytý soubor
    except OSError:
        loguj(traceback.format_exc())
        toast("Párování se nepovedlo",
              f"Na disk {koren} se nepodařilo zapsat. Není jen pro čtení?")
        return
    toast("USB disk spárován",
          f"Disk {koren} je teď můj. Spouštím první synchronizaci.")
    # hlídač si disk převezme; sync spustíme rovnou, ať se nečeká
    if koren not in S["usb"]:
        S["usb"] = S["usb"] + [koren]
    nastav_stav("ok")
    akce_sync()


def autostart_zapnuty(_polozka=None):
    if ZABALENO:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KLIC) as klic:
                winreg.QueryValueEx(klic, "Holub")
            return True
        except OSError:
            return False
    return os.path.isfile(CESTA_AUTOSTART)


def _autostart_registr(zapnout):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KLIC, 0,
                        winreg.KEY_SET_VALUE) as klic:
        if zapnout:
            winreg.SetValueEx(klic, "Holub", 0, winreg.REG_SZ,
                              '"%s"' % sys.executable)
        else:
            winreg.DeleteValue(klic, "Holub")


def akce_autostart(ikona=None, _polozka=None):
    if autostart_zapnuty():
        try:
            if ZABALENO:
                _autostart_registr(False)
            else:
                os.remove(CESTA_AUTOSTART)
            toast("Automatické spouštění vypnuto")
        except OSError:
            loguj(traceback.format_exc())
    else:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.isfile(pythonw):
            pythonw = sys.executable
        skript = os.path.join(SLOZKA, "holub.py")
        obsah = ("import subprocess\n"
                 f'subprocess.Popen([r"{pythonw}", r"{skript}"], cwd=r"{SLOZKA}")\n')
        try:
            if ZABALENO:
                _autostart_registr(True)
            else:
                with open(CESTA_AUTOSTART, "w", encoding="utf-8") as soubor:
                    soubor.write(obsah)
            toast("Automatické spouštění zapnuto",
                  "Holub se teď spustí po každém přihlášení do Windows.")
        except OSError:
            loguj(traceback.format_exc())
            toast("Nepodařilo se zapnout automatické spouštění",
                  "Podrobnosti jsou v souboru holub.log.")
    if ikona:
        ikona.update_menu()


def akce_konec(ikona, _polozka=None):
    S["bezi"] = False
    if OKNO["fronta"] is not None:
        OKNO["fronta"].put("konec")
    ikona.stop()

# ---------------------------------------------------------------------------
# Vlákna na pozadí: hlídač USB + animace letu
# ---------------------------------------------------------------------------


def hlidac():
    """Každých pár sekund se podívá, jestli je spárovaný disk připojený.
    Jen dotaz do Windows na seznam disků — nic nečte ani nekopíruje."""
    while S["bezi"]:
        # zmizel vault? (přejmenování, přesun) → červená ikona, nikdy nesyncovat naslepo
        if CFG.get("vault") and not os.path.isdir(CFG["vault"]):
            if not S["vault_chybi"]:
                S["vault_chybi"] = True
                nastav_stav("chyba", "nenacházím složku vaultu")
                toast("Nenacházím složku vaultu",
                      "Složka se nejspíš přesunula. V menu zvol "
                      "„Zvolit složku vaultu…“ a vyber ji znovu.")
        elif S["vault_chybi"]:
            S["vault_chybi"] = False
            nastav_stav("ok" if S["usb"] else "ceka")

        disky = najdi_usb_vsechny()
        nove = [disk for disk in disky if disk not in S["usb"]]
        byly = bool(S["usb"])
        S["usb"] = disky
        if nove and not S["vault_chybi"]:
            nastav_stav("ok")
            akce_sync()  # sync při zasunutí (obslouží všechny připojené)
        elif not disky and byly:
            if S["stav"] not in ("sync", "chyba"):
                nastav_stav("ceka")
        time.sleep(max(2, int(CFG.get("interval_kontroly_s", 5))))


def nastav_casovac(rezim, minuty=None):
    CFG["casovac"] = rezim
    if minuty is not None:
        CFG["casovac_minuty"] = minuty
    uloz_config()
    if IKONA["obj"]:
        IKONA["obj"].update_menu()


def _cas_denne():
    """Cíl denní synchronizace jako (hodina, minuta), nebo None."""
    try:
        hodina, minuta = CFG.get("casovac_denne", "18:00").split(":")
        return int(hodina), int(minuta)
    except ValueError:
        return None


def casovac_smycka():
    """Automatická synchronizace podle času (menu ⏰).

    Interval se počítá od posledního ÚSPĚŠNÉHO syncu (jedno jestli ručního,
    po zasunutí, nebo z časovače) — takže se nesyncuje častěji, než je třeba.
    Bez připojeného USB časovač mlčky čeká, žádné otravné chybové toasty."""
    ted = datetime.now()
    cil = _cas_denne()
    if cil and (ted.hour, ted.minute) >= cil:
        # dnešní čas už proběhl před spuštěním appky — nedohánět,
        # sync při startu obstará hlídač USB
        S["casovac_den"] = ted.date().isoformat()
    while S["bezi"]:
        time.sleep(20)
        rezim = CFG.get("casovac", "vypnuto")
        muze = (S["usb"] and not S["vault_chybi"] and S["stav"] != "sync"
                and not S["mazani_ceka"])
        if rezim == "interval":
            try:
                minuty = max(1, int(CFG.get("casovac_minuty", 30)))
            except (TypeError, ValueError):
                minuty = 30
            posledni = S["casovac_pokus"]
            try:
                posledni = max(posledni, datetime.fromisoformat(
                    CFG.get("posledni_sync", "")).timestamp())
            except ValueError:
                pass
            if muze and time.time() - posledni >= minuty * 60:
                S["casovac_pokus"] = time.time()
                akce_sync()
        elif rezim == "denne":
            cil = _cas_denne()
            ted = datetime.now()
            dnes = ted.date().isoformat()
            if cil and S["casovac_den"] != dnes and (ted.hour, ted.minute) >= cil:
                S["casovac_den"] = dnes  # dnešek odbytý i bez USB — dosync udělá hlídač
                if muze:
                    akce_sync()


def animace():
    """Za letu holub mává křídly; v klidu si občas žije po svém —
    mrkne, nebo klovne po něčem na zemi."""
    horni = False
    dalsi_kousek = time.time() + random.uniform(5, 12)
    while S["bezi"]:
        ikona = IKONA["obj"]
        if S["stav"] == "sync" and ikona:
            horni = not horni
            try:
                ikona.icon = IKONY["let1" if horni else "let2"]
            except Exception:
                pass
        elif S["stav"] == "ok" and ikona and time.time() >= dalsi_kousek:
            try:
                if random.random() < 0.5:
                    ikona.icon = IKONY["mrk"]
                    time.sleep(0.15)
                else:
                    for snimek in ("klov", "ok", "klov"):
                        if S["stav"] != "ok":
                            break
                        ikona.icon = IKONY[snimek]
                        time.sleep(0.22)
                if S["stav"] == "ok":
                    ikona.icon = IKONY["ok"]
            except Exception:
                pass
            dalsi_kousek = time.time() + random.uniform(6, 15)
        time.sleep(0.3)


def cekac_na_prehled():
    """Čeká na „zazvonění" od tlačítka na oznámení a otevře okno Přehledu."""
    udalost = ctypes.windll.kernel32.CreateEventW(None, False, False,
                                                  "Holub-ukaz-prehled")
    if not udalost:
        return
    while S["bezi"]:
        if ctypes.windll.kernel32.WaitForSingleObject(udalost, 1000) == 0:
            akce_okno()

# ---------------------------------------------------------------------------
# Okno „Přehled" — historie, konflikty, kontrola změn. Tmavý vzhled.
#
# tkinter musí žít celý v jednom vlákně, proto má okno vlastní trvalé vlákno
# a ostatní vlákna s ním mluví jen přes frontu OKNO["fronta"] a sdílené
# slovníky (NAHLED, KONFLIKTY), které si okno samo periodicky čte.
# ---------------------------------------------------------------------------

BARVY = {
    "pozadi": "#1f2127", "karta": "#282b33", "text": "#e8eaf0",
    "tlumena": "#9aa1ad", "akcent": "#3fae7a", "cervena": "#e06c6c",
    "tlacitko": "#2f333c", "tlacitko_aktivni": "#3a3f4a", "vyber": "#3a3f4a",
    "nebezpeci": "#8c3a3a",
}

OKNO = {"fronta": None}
NAHLED = {"text": ""}
KONFLIKTY = {"seznam": [], "verze": 0, "hledam": False}
POTVRZENI = {"info": None}  # čekající dotaz pojistky hromadného mazání


def zajisti_vlakno_okna():
    if OKNO["fronta"] is None:
        OKNO["fronta"] = queue.Queue()
        threading.Thread(target=vlakno_okna, daemon=True).start()


def akce_okno(_ikona=None, _polozka=None):
    """Otevře okno Přehledu (nebo ho vytáhne dopředu, když už existuje)."""
    zajisti_vlakno_okna()
    OKNO["fronta"].put("ukaz")


def akce_casovac_dialog(druh):
    """Otevře dialog pro vlastní interval ("interval") nebo denní čas ("denne")."""
    zajisti_vlakno_okna()
    OKNO["fronta"].put("dialog-" + druh)


def akce_potvrzeni_mazani():
    """Otevře dotaz pojistky: opravdu smazat tolik poznámek najednou?"""
    zajisti_vlakno_okna()
    OKNO["fronta"].put("dialog-mazani")


def najdi_konflikty():
    """Na pozadí projde vault a sesbírá konfliktní kopie poznámek."""
    if KONFLIKTY["hledam"]:
        return
    KONFLIKTY["hledam"] = True

    def hledej():
        nalezene = []
        vault = CFG.get("vault")
        if vault and os.path.isdir(vault):
            for cesta, _slozky, jmena in os.walk(vault):
                for jmeno in jmena:
                    if " (konflikt z USB" in jmeno:
                        nalezene.append(os.path.relpath(
                            os.path.join(cesta, jmeno), vault).replace("\\", "/"))
        KONFLIKTY["seznam"] = sorted(nalezene)
        KONFLIKTY["verze"] += 1
        KONFLIKTY["hledam"] = False

    threading.Thread(target=hledej, daemon=True).start()


def zkontroluj_zmeny():
    """Spočítá nanečisto, co by synchronizace udělala — nic nekopíruje."""
    if not ZAMEK_SYNCU.acquire(blocking=False):
        NAHLED["text"] = "Právě probíhá synchronizace…"
        return
    try:
        vault = CFG.get("vault")
        disky = list(S["usb"]) or najdi_usb_vsechny()
        if not vault or not os.path.isdir(vault):
            NAHLED["text"] = "Nenacházím složku vaultu."
            return
        if not disky:
            NAHLED["text"] = "USB disk není připojený."
            return
        ignorovat = set(CFG.get("ignorovat", []))
        casti = []
        for disk in disky:
            cil = os.path.join(disk, SLOZKA_ZALOHY)
            if CFG.get("rezim") == "obousmerny":
                vysledek = sync_obousmerny(vault, cil,
                                           os.path.join(disk, SOUBOR_SNIMKU),
                                           ignorovat, naostro=False)
                nic = all(pocet == 0 for pocet in vysledek.values())
                cast = ("vše je synchronizované" if nic
                        else "čeká: " + zprava_obousmerna(vysledek))
            else:
                zkopirovano, smazano = sync_jednosmerny(vault, cil, ignorovat,
                                                        naostro=False)
                cast = ("vše je synchronizované"
                        if not zkopirovano and not smazano
                        else "čeká: " + zprava_jednosmerna(zkopirovano, smazano))
            casti.append(cast if len(disky) == 1 else f"{disk[0]}: {cast}")
        text = " | ".join(casti)
        NAHLED["text"] = text[0].upper() + text[1:]
    except Exception:
        loguj(traceback.format_exc())
        NAHLED["text"] = "Kontrola se nepovedla — podrobnosti v holub.log."
    finally:
        ZAMEK_SYNCU.release()


def vlakno_okna():
    import tkinter as tk
    from tkinter import messagebox

    try:  # ostré vykreslení na displejích se zvětšením
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    B = BARVY
    koren = tk.Tk()
    koren.withdraw()
    prvky = {}  # widgety a obrázky (obrázky tu musí zůstat, jinak je Tk pustí)
    try:  # holub v titulku — zdědí ho okno Přehledu i dialogy
        zajisti_toast_ikonu()
        znak = tk.PhotoImage(file=CESTA_TOAST_IKONY)
        koren.iconphoto(True, znak)
        prvky["znak"] = znak
        prvky["znak_maly"] = znak.subsample(2)
    except Exception:
        pass

    def ztmav_titulek(okno):
        """Řekne Windows, ať je horní lišta okna tmavá (DWM atribut 20)."""
        try:
            okno.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(okno.winfo_id())
            hodnota = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(hodnota), ctypes.sizeof(hodnota))
        except Exception:
            pass

    def tlacitko(rodic, text, prikaz, barva=None):
        return tk.Button(
            rodic, text=text, command=prikaz, relief="flat", bd=0,
            bg=barva or B["tlacitko"], fg=B["text"],
            activebackground=barva or B["tlacitko_aktivni"],
            activeforeground=B["text"],
            font=("Segoe UI", 10), padx=12, pady=6, cursor="hand2")

    def nadpis(rodic, text):
        return tk.Label(rodic, text=text, bg=B["pozadi"], fg=B["text"],
                        font=("Segoe UI Semibold", 11), anchor="w")

    def seznam_widget(rodic, vyska):
        return tk.Listbox(
            rodic, bg=B["karta"], fg=B["text"], selectbackground=B["vyber"],
            selectforeground=B["text"], font=("Segoe UI", 10), bd=0,
            highlightthickness=0, activestyle="none", height=vyska)

    def postav():
        okno = tk.Toplevel(koren)
        okno.title("Holub — přehled")
        okno.configure(bg=B["pozadi"])
        okno.geometry("700x680+200+120")
        okno.minsize(640, 560)
        okno.protocol("WM_DELETE_WINDOW", okno.withdraw)  # zavření jen schová
        ztmav_titulek(okno)

        # hlavička: holub + stavový řádek
        hlava = tk.Frame(okno, bg=B["pozadi"])
        hlava.pack(fill="x", padx=16, pady=(14, 8))
        if "znak_maly" in prvky:
            tk.Label(hlava, image=prvky["znak_maly"], bg=B["pozadi"]).pack(
                side="left", padx=(0, 12))
        texty = tk.Frame(hlava, bg=B["pozadi"])
        texty.pack(side="left")
        tk.Label(texty, text="Holub", bg=B["pozadi"], fg=B["text"],
                 font=("Segoe UI Semibold", 16), anchor="w").pack(anchor="w")
        prvky["stav"] = tk.Label(texty, text="", bg=B["pozadi"],
                                 fg=B["tlumena"], font=("Segoe UI", 10),
                                 anchor="w")
        prvky["stav"].pack(anchor="w")

        # řada tlačítek + řádek s výsledkem kontroly
        rada = tk.Frame(okno, bg=B["pozadi"])
        rada.pack(fill="x", padx=16, pady=(0, 2))
        tlacitko(rada, "🔄  Synchronizovat teď", akce_sync).pack(
            side="left", padx=(0, 8))

        def spust_kontrolu():
            NAHLED["text"] = "Počítám…"
            threading.Thread(target=zkontroluj_zmeny, daemon=True).start()

        tlacitko(rada, "🔍  Zkontrolovat změny", spust_kontrolu).pack(
            side="left", padx=(0, 8))
        tlacitko(rada, "📁  Otevřít zálohu", akce_otevrit_zalohu).pack(side="left")
        prvky["nahled"] = tk.Label(okno, text="", bg=B["pozadi"],
                                   fg=B["akcent"], font=("Segoe UI", 10),
                                   anchor="w")
        prvky["nahled"].pack(fill="x", padx=18, pady=(4, 8))

        # konflikty — kotví se ke spodnímu okraji, aby je nic nevytlačilo ven
        rada_konflikty = tk.Frame(okno, bg=B["pozadi"])
        rada_konflikty.pack(side="bottom", fill="x", padx=16, pady=(0, 14))
        ram_konflikty = tk.Frame(okno, bg=B["karta"])
        ram_konflikty.pack(side="bottom", fill="x", padx=16, pady=(6, 6))
        nadpis(okno, "Konflikty k vyřešení").pack(side="bottom", fill="x", padx=16)
        prvky["konflikty"] = seznam_widget(ram_konflikty, 3)
        prvky["konflikty"].pack(fill="x", padx=8, pady=6)
        tlacitko(rada_konflikty, "Otevřít kopii",
                 lambda: otevri_konflikt(puvodni=False)).pack(side="left", padx=(0, 8))
        tlacitko(rada_konflikty, "Otevřít původní",
                 lambda: otevri_konflikt(puvodni=True)).pack(side="left", padx=(0, 8))
        tlacitko(rada_konflikty, "Smazat kopii…", smaz_konflikt).pack(side="left")

        # historie synchronizací — vyplní zbytek okna
        nadpis(okno, "Historie synchronizací").pack(fill="x", padx=16)
        ram_historie = tk.Frame(okno, bg=B["karta"])
        ram_historie.pack(fill="both", expand=True, padx=16, pady=(6, 10))
        prvky["historie"] = seznam_widget(ram_historie, 6)
        posuvnik = tk.Scrollbar(ram_historie, command=prvky["historie"].yview)
        prvky["historie"].config(yscrollcommand=posuvnik.set)
        posuvnik.pack(side="right", fill="y")
        prvky["historie"].pack(fill="both", expand=True, padx=8, pady=6)

        prvky["okno"] = okno
        prvky["mtime_historie"] = "nikdy"
        prvky["verze_konfliktu"] = -1

    def vybrany_konflikt():
        vyber = prvky["konflikty"].curselection()
        if not vyber or vyber[0] >= len(KONFLIKTY["seznam"]):
            return None
        return KONFLIKTY["seznam"][vyber[0]]

    def otevri_konflikt(puvodni):
        rel = vybrany_konflikt()
        if rel is None:
            return
        if puvodni:  # z „k (konflikt z USB).md" udělá zpět „k.md"
            rel = rel.split(" (konflikt z USB")[0] + os.path.splitext(rel)[1]
        plna = os.path.join(CFG.get("vault", ""), rel)
        if os.path.exists(plna):
            os.startfile(plna)

    def smaz_konflikt():
        rel = vybrany_konflikt()
        if rel is None:
            return
        if messagebox.askyesno(
                "Smazat konfliktní kopii?",
                f"Opravdu smazat „{rel}“?\n\nUdělej to, až budeš mít obsah obou "
                "verzí srovnaný — smazání nejde vrátit.",
                parent=prvky["okno"]):
            try:
                os.remove(os.path.join(CFG.get("vault", ""), rel))
            except OSError:
                loguj(traceback.format_exc())
            najdi_konflikty()

    def prekresli_historii():
        seznam = prvky["historie"]
        seznam.delete(0, "end")
        zaznamy = nacti_historii()
        if not zaznamy:
            seznam.insert("end", "  zatím žádná synchronizace")
            seznam.itemconfig(0, fg=B["tlumena"])
            return
        for zaznam in reversed(zaznamy):
            sipka = "⇄" if zaznam.get("rezim") == "obousmerny" else "→"
            seznam.insert("end", f"  {hezky_cas(zaznam.get('kdy', ''))}   "
                                 f"{sipka}   {zaznam.get('zprava', '')}")
            if zaznam.get("chyba"):
                seznam.itemconfig("end", fg=B["cervena"])

    def prekresli_konflikty():
        seznam = prvky["konflikty"]
        seznam.delete(0, "end")
        if not KONFLIKTY["seznam"]:
            seznam.insert("end", "  žádné konflikty — všechno v klidu")
            seznam.itemconfig(0, fg=B["tlumena"])
            return
        for rel in KONFLIKTY["seznam"]:
            seznam.insert("end", "  " + rel)

    def obnov():
        """Periodické překreslení z sdíleného stavu (běží jen když je okno vidět)."""
        if "okno" in prvky and prvky["okno"].winfo_viewable():
            prvky["stav"].config(text=stavovy_text())
            prvky["nahled"].config(text=NAHLED["text"])
            try:
                mtime = os.path.getmtime(CESTA_HISTORIE)
            except OSError:
                mtime = None
            if mtime != prvky["mtime_historie"]:
                prvky["mtime_historie"] = mtime
                prekresli_historii()
                najdi_konflikty()
            if prvky["verze_konfliktu"] != KONFLIKTY["verze"]:
                prvky["verze_konfliktu"] = KONFLIKTY["verze"]
                prekresli_konflikty()
        koren.after(700, obnov)

    def zeptej(druh):
        """Malý tmavý dialog: vlastní interval v minutách / denní čas HH:MM."""
        dialog = tk.Toplevel(koren)
        dialog.title("Automatická synchronizace")
        dialog.configure(bg=B["pozadi"])
        dialog.resizable(False, False)
        dialog.geometry("+340+280")
        ztmav_titulek(dialog)
        if druh == "interval":
            popis = "Jak často se má synchronizovat?\nZadej počet minut (1–1440):"
            vychozi = str(CFG.get("casovac_minuty", 30))
        else:
            popis = ("V kolik hodin se má každý den synchronizovat?\n"
                     "Zadej čas jako HH:MM (třeba 18:00):")
            vychozi = CFG.get("casovac_denne", "18:00")
        tk.Label(dialog, text=popis, bg=B["pozadi"], fg=B["text"],
                 font=("Segoe UI", 10), justify="left").pack(
            padx=16, pady=(14, 6), anchor="w")
        pole = tk.Entry(dialog, bg=B["karta"], fg=B["text"],
                        insertbackground=B["text"], relief="flat",
                        font=("Segoe UI", 11), width=10)
        pole.insert(0, vychozi)
        pole.pack(padx=16, pady=4, anchor="w", ipady=4, ipadx=6)
        varovani = tk.Label(dialog, text="", bg=B["pozadi"], fg=B["cervena"],
                            font=("Segoe UI", 9))
        varovani.pack(padx=16, anchor="w")

        def potvrd():
            zadani = pole.get().strip()
            if druh == "interval":
                try:
                    minuty = int(zadani)
                except ValueError:
                    minuty = 0
                if not 1 <= minuty <= 1440:
                    varovani.config(text="Zadej celé číslo od 1 do 1440.")
                    return
                CFG["casovac"] = "interval"
                CFG["casovac_minuty"] = minuty
            else:
                casti = zadani.split(":")
                try:
                    hodina, minuta = int(casti[0]), int(casti[1])
                except (ValueError, IndexError):
                    hodina = -1
                    minuta = -1
                if not (0 <= hodina <= 23 and 0 <= minuta <= 59):
                    varovani.config(text="Zadej čas jako HH:MM, třeba 18:00.")
                    return
                CFG["casovac"] = "denne"
                CFG["casovac_denne"] = f"{hodina:02d}:{minuta:02d}"
                ted = datetime.now()  # dnešní už proběhlý čas nedohánět
                S["casovac_den"] = (ted.date().isoformat()
                                    if (ted.hour, ted.minute) >= (hodina, minuta)
                                    else "")
            uloz_config()
            if IKONA["obj"]:
                IKONA["obj"].update_menu()
            dialog.destroy()

        rada = tk.Frame(dialog, bg=B["pozadi"])
        rada.pack(fill="x", padx=16, pady=(6, 14))
        tlacitko(rada, "Uložit", potvrd).pack(side="right", padx=(8, 0))
        tlacitko(rada, "Zrušit", dialog.destroy).pack(side="right")
        pole.bind("<Return>", lambda _u: potvrd())
        dialog.attributes("-topmost", True)  # přichází z tray menu, ať nezapadne
        dialog.lift()
        pole.focus_force()

    def zeptej_mazani():
        """Dotaz pojistky: opravdu smazat tolik poznámek najednou?"""
        info = POTVRZENI["info"]
        if info is None or prvky.get("dialog_mazani"):
            return
        dialog = tk.Toplevel(koren)
        prvky["dialog_mazani"] = dialog
        dialog.title("Holub — opravdu smazat?")
        dialog.configure(bg=B["pozadi"])
        dialog.resizable(False, False)
        dialog.geometry("+320+260")
        ztmav_titulek(dialog)
        zprava = (f"Synchronizace se chystala smazat {info['kolik']} "
                  f"z {info['celkem']} poznámek {info['kde']} (disk {info['disk']}).\n\n"
                  "To je hodně najednou, tak jsem se raději zastavil.\n"
                  "Nepřesunula se ti složka vaultu? Nezmizely poznámky omylem?\n\n"
                  f"Smazané neskončí v nenávratnu — jdou do koše {KOS_SLOZKA}\n"
                  f"a tam se drží {KOS_DNY} dní.")
        tk.Label(dialog, text=zprava, bg=B["pozadi"], fg=B["text"],
                 font=("Segoe UI", 10), justify="left").pack(
            padx=18, pady=(16, 10), anchor="w")

        def zavri(smazat):
            S["mazani_ceka"] = False
            POTVRZENI["info"] = None
            prvky.pop("dialog_mazani", None)
            dialog.destroy()
            if smazat:
                S["povolit_mazani"] = True  # platí pro následující jeden běh
                akce_sync()
            else:
                nastav_stav("ok" if S["usb"] else "ceka")

        rada = tk.Frame(dialog, bg=B["pozadi"])
        rada.pack(fill="x", padx=18, pady=(4, 16))
        tlacitko(rada, "Smazat a synchronizovat", lambda: zavri(True),
                 barva=B["nebezpeci"]).pack(side="right", padx=(8, 0))
        tlacitko(rada, "Zrušit", lambda: zavri(False)).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", lambda: zavri(False))
        dialog.attributes("-topmost", True)
        dialog.lift()

    def zpracuj_frontu():
        try:
            while True:
                prikaz = OKNO["fronta"].get_nowait()
                if prikaz == "konec":
                    koren.quit()
                    return
                if prikaz == "ukaz":
                    if "okno" not in prvky:
                        postav()
                        prekresli_historii()
                        najdi_konflikty()
                    prvky["okno"].deiconify()
                    prvky["okno"].lift()
                    try:
                        prvky["okno"].focus_force()
                    except Exception:
                        pass
                elif prikaz == "dialog-interval":
                    zeptej("interval")
                elif prikaz == "dialog-denne":
                    zeptej("denne")
                elif prikaz == "dialog-mazani":
                    zeptej_mazani()
        except queue.Empty:
            pass
        koren.after(200, zpracuj_frontu)

    koren.after(100, zpracuj_frontu)
    koren.after(400, obnov)
    koren.mainloop()

# ---------------------------------------------------------------------------
# Menu a start
# ---------------------------------------------------------------------------


def text_jineho_intervalu(_polozka=None):
    if (CFG.get("casovac") == "interval"
            and CFG.get("casovac_minuty") not in (15, 30, 60)):
        return f"Jiný interval ({CFG['casovac_minuty']} min)…"
    return "Jiný interval…"


def text_denniho_casu(_polozka=None):
    return f"Každý den v {CFG.get('casovac_denne', '18:00')}…"


def je_interval(minuty):
    return (CFG.get("casovac") == "interval"
            and CFG.get("casovac_minuty") == minuty)


def postav_menu():
    return pystray.Menu(
        pystray.MenuItem("Holub", None, enabled=False),
        pystray.MenuItem(stavovy_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🪟 Přehled a historie", akce_okno, default=True),
        pystray.MenuItem("🔄 Synchronizovat teď", akce_sync),
        pystray.MenuItem("⇄ Režim synchronizace", pystray.Menu(
            pystray.MenuItem(
                "Jednosměrný (PC → USB)",
                lambda *_: nastav_rezim("jednosmerny"),
                checked=lambda _p: CFG.get("rezim") == "jednosmerny",
                radio=True),
            pystray.MenuItem(
                "Obousměrný (PC ⇄ USB)",
                lambda *_: nastav_rezim("obousmerny"),
                checked=lambda _p: CFG.get("rezim") == "obousmerny",
                radio=True),
        )),
        pystray.MenuItem("⏰ Automatická synchronizace", pystray.Menu(
            pystray.MenuItem(
                "Vypnutá", lambda *_: nastav_casovac("vypnuto"),
                checked=lambda _p: CFG.get("casovac", "vypnuto") == "vypnuto",
                radio=True),
            pystray.MenuItem(
                "Každých 15 minut", lambda *_: nastav_casovac("interval", 15),
                checked=lambda _p: je_interval(15), radio=True),
            pystray.MenuItem(
                "Každých 30 minut", lambda *_: nastav_casovac("interval", 30),
                checked=lambda _p: je_interval(30), radio=True),
            pystray.MenuItem(
                "Každou hodinu", lambda *_: nastav_casovac("interval", 60),
                checked=lambda _p: je_interval(60), radio=True),
            pystray.MenuItem(
                text_jineho_intervalu,
                lambda *_: akce_casovac_dialog("interval"),
                checked=lambda _p: (CFG.get("casovac") == "interval"
                                    and CFG.get("casovac_minuty")
                                    not in (15, 30, 60)),
                radio=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                text_denniho_casu, lambda *_: akce_casovac_dialog("denne"),
                checked=lambda _p: CFG.get("casovac") == "denne", radio=True),
        )),
        pystray.MenuItem("📁 Otevřít zálohu na USB", akce_otevrit_zalohu),
        pystray.MenuItem("📂 Zvolit složku vaultu…", akce_zvolit_vault),
        pystray.MenuItem("🔌 Spárovat nový USB disk…", akce_parovat),
        pystray.MenuItem("Spouštět se systémem Windows", akce_autostart,
                         checked=lambda _p: autostart_zapnuty()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("✕ Ukončit", akce_konec),
    )


def po_startu(ikona):
    ikona.visible = True
    threading.Thread(target=hlidac, daemon=True).start()
    threading.Thread(target=animace, daemon=True).start()
    threading.Thread(target=casovac_smycka, daemon=True).start()
    threading.Thread(target=cekac_na_prehled, daemon=True).start()
    if not CFG.get("vault"):
        toast("Ahoj, tady Holub 🕊️",
              "Budu ti zálohovat poznámky na USB. Nejdřív mi ukaž složku vaultu.")
        akce_zvolit_vault()


def main():
    if "--dialog-slozka" in sys.argv:  # pomocný režim pro výběr složky
        i = sys.argv.index("--dialog-slozka")
        dialog_slozka_proces(sys.argv[i + 1], sys.argv[i + 2])
        return
    try:  # vlastní identita na hlavním panelu — jinak si lišta půjčí ikonu Pythonu
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Holub")
    except Exception:
        pass
    # jen jedna instance naráz
    ctypes.windll.kernel32.CreateMutexW(None, False, "Holub-USB-sync")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        if "--ukaz-prehled" in sys.argv:  # z tlačítka na oznámení
            udalost = ctypes.windll.kernel32.CreateEventW(
                None, False, False, "Holub-ukaz-prehled")
            ctypes.windll.kernel32.SetEvent(udalost)
            return
        zajisti_toast_ikonu()
        toast("Holub už běží", "Ikonu najdeš v liště u hodin.")
        return
    nacti_config()
    zajisti_toast_ikonu()
    IKONA["obj"] = pystray.Icon("Holub", IKONY["ceka"], "Holub", postav_menu())
    IKONA["obj"].run(setup=po_startu)


if __name__ == "__main__":
    main()

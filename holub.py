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

SLOZKA = os.path.dirname(os.path.abspath(__file__))
CESTA_CONFIG = os.path.join(SLOZKA, "config.json")
if "--config" in sys.argv:  # jiný config pro testování na cvičném vaultu
    CESTA_CONFIG = sys.argv[sys.argv.index("--config") + 1]

CESTA_LOG = os.path.join(SLOZKA, "holub.log")
CESTA_TOAST_IKONY = os.path.join(SLOZKA, "holub-ikona.png")
CESTA_AUTOSTART = os.path.join(
    os.environ.get("APPDATA", ""), "Microsoft", "Windows",
    "Start Menu", "Programs", "Startup", "Holub.pyw")

ZNACKA_USB = ".holub-usb"              # párovací soubor v kořeni USB disku
SLOZKA_ZALOHY = "Vault"                # složka s kopií vaultu na USB
SOUBOR_SNIMKU = "holub-snapshot.json"  # paměť posledního syncu (obousměrný režim)
TOLERANCE_CASU = 3                     # s — FAT32 ukládá časy jen po 2 s

VYCHOZI_CONFIG = {
    "vault": "",
    "rezim": "jednosmerny",            # "jednosmerny" | "obousmerny"
    "interval_kontroly_s": 5,
    "ignorovat": [".obsidian/workspace.json", ".obsidian/workspace-mobile.json"],
    "posledni_sync": "",
}

CFG = dict(VYCHOZI_CONFIG)

# Sdílený stav appky. "stav" je jedno z: ceka / ok / sync / chyba / hotovo
S = {"stav": "ceka", "usb": None, "bezi": True, "chyba_text": "", "vault_chybi": False}
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


def toast(titulek, text=""):
    try:
        Notification(app_id="Holub", title=titulek, msg=text,
                     icon=CESTA_TOAST_IKONY).show()
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
    kod = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "okno = tk.Tk(); okno.withdraw(); okno.attributes('-topmost', True)\n"
        f"print(filedialog.askdirectory(title={titulek!r}) or '')\n"
    )
    prostredi = dict(os.environ, PYTHONIOENCODING="utf-8")
    vysledek = subprocess.run(
        [sys.executable, "-c", kod], capture_output=True, text=True,
        encoding="utf-8", env=prostredi,
        creationflags=subprocess.CREATE_NO_WINDOW)
    cesta = (vysledek.stdout or "").strip()
    return os.path.normpath(cesta) if cesta else None

# ---------------------------------------------------------------------------
# Detekce USB disku
# ---------------------------------------------------------------------------


def pripojene_disky():
    maska = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{chr(65 + i)}:\\" for i in range(26) if maska >> i & 1]


def najdi_usb():
    """Vrátí kořen prvního disku se značkou .holub-usb, jinak None."""
    system = (os.environ.get("SystemDrive", "C:") + "\\").upper()
    for disk in pripojene_disky():
        if disk.upper() == system:
            continue
        try:
            if os.path.isfile(os.path.join(disk, ZNACKA_USB)):
                return disk
        except OSError:
            pass
    return None

# ---------------------------------------------------------------------------
# Synchronizace — jádro appky
#
# Bezpečnostní pravidla z plan.md:
#   1. Jednosměrný režim NIKDY nezapisuje ani nemaže na PC.
#   2. Obousměrný režim NIKDY potichu nepřepisuje konflikt.
#   3. Nikdy nesyncovat bez platné párovací značky na disku.
# ---------------------------------------------------------------------------


def projdi(koren, ignorovat):
    """Vrátí {relativní cesta: (čas úpravy, velikost)} všech souborů ve složce."""
    soubory = {}
    for cesta, _slozky, jmena in os.walk(koren):
        for jmeno in jmena:
            plna = os.path.join(cesta, jmeno)
            rel = os.path.relpath(plna, koren).replace("\\", "/")
            if rel in ignorovat:
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


def sync_jednosmerny(vault, cil, ignorovat):
    """Zrcadlo PC → USB. Na PC nesahá — jen čte."""
    os.makedirs(cil, exist_ok=True)
    na_pc = projdi(vault, ignorovat)
    na_usb = projdi(cil, ignorovat)

    zkopirovano = 0
    for rel, udaje in na_pc.items():
        if rel not in na_usb or not stejne(udaje, na_usb[rel]):
            zkopiruj(vault, cil, rel)
            zkopirovano += 1

    smazano = 0
    for rel in na_usb:
        if rel not in na_pc:
            os.remove(os.path.join(cil, rel))
            smazano += 1
    if smazano:
        smaz_prazdne_slozky(cil)
    return zkopirovano, smazano


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


def sync_obousmerny(vault, cil, cesta_snimku, ignorovat):
    """PC ⇄ USB. Snímek posledního syncu rozlišuje „smazáno" od „nové"."""
    os.makedirs(cil, exist_ok=True)
    na_pc = projdi(vault, ignorovat)
    na_usb = projdi(cil, ignorovat)

    snimek = {}
    if os.path.isfile(cesta_snimku):
        try:
            with open(cesta_snimku, encoding="utf-8") as soubor:
                snimek = {rel: tuple(udaje) for rel, udaje in json.load(soubor).items()}
        except (OSError, ValueError):
            snimek = {}  # poškozený snímek → nic se nemaže, jen se slévá obsah

    vysledek = {"na_usb": 0, "na_pc": 0, "smazano_usb": 0, "smazano_pc": 0,
                "konflikty": 0}

    for rel in sorted(set(na_pc) | set(na_usb)):
        je_pc, je_usb = rel in na_pc, rel in na_usb
        zmena_pc = je_pc and (rel not in snimek or not stejne(na_pc[rel], snimek[rel]))
        zmena_usb = je_usb and (rel not in snimek or not stejne(na_usb[rel], snimek[rel]))

        if je_pc and je_usb:
            if stejne(na_pc[rel], na_usb[rel]):
                continue
            if zmena_pc and zmena_usb:
                # Konflikt: verze z PC si nechá původní jméno, verze z USB
                # se uloží vedle ní — na obou stranách, nic se neztratí.
                kopie = konfliktni_jmeno(rel, vault, cil)
                shutil.copy2(os.path.join(cil, rel), os.path.join(cil, kopie))
                shutil.copy2(os.path.join(cil, rel), os.path.join(vault, kopie))
                zkopiruj(vault, cil, rel)
                vysledek["konflikty"] += 1
            elif zmena_pc:
                zkopiruj(vault, cil, rel)
                vysledek["na_usb"] += 1
            elif zmena_usb:
                zkopiruj(cil, vault, rel)
                vysledek["na_pc"] += 1
            else:
                # Hraniční případ (snímek sedí na obě strany, přesto se liší):
                # vyhrává novější verze.
                if na_pc[rel][0] >= na_usb[rel][0]:
                    zkopiruj(vault, cil, rel)
                    vysledek["na_usb"] += 1
                else:
                    zkopiruj(cil, vault, rel)
                    vysledek["na_pc"] += 1
        elif je_pc:
            if rel in snimek and not zmena_pc:
                os.remove(os.path.join(vault, rel))   # smazáno na USB
                vysledek["smazano_pc"] += 1
            else:
                zkopiruj(vault, cil, rel)             # nové na PC (úprava poráží smazání)
                vysledek["na_usb"] += 1
        else:
            if rel in snimek and not zmena_usb:
                os.remove(os.path.join(cil, rel))     # smazáno na PC
                vysledek["smazano_usb"] += 1
            else:
                zkopiruj(cil, vault, rel)             # nové na USB
                vysledek["na_pc"] += 1

    if vysledek["smazano_usb"]:
        smaz_prazdne_slozky(cil)
    if vysledek["smazano_pc"]:
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
    if S["usb"] is None:
        return "čekám na USB disk"
    if CFG.get("posledni_sync"):
        return "vše synchronizováno · " + hezky_cas(CFG["posledni_sync"])
    return "USB připojené · zatím nesynchronizováno"


def formatuj_cas(sekundy):
    if sekundy < 10:
        return f"{sekundy:.1f} s".replace(".", ",")
    return f"{round(sekundy)} s"


def zprava_jednosmerna(zkopirovano, smazano, trvani):
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
    return " · ".join(casti + [formatuj_cas(trvani)])


def zprava_obousmerna(vysledek, trvani):
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
    return " · ".join(casti + [formatuj_cas(trvani)])


def synchronizuj():
    """Jeden běh synchronizace. Volat vždy v samostatném vlákně."""
    if not ZAMEK_SYNCU.acquire(blocking=False):
        return  # už běží
    usb = None
    try:
        vault = CFG.get("vault")
        if not vault or not os.path.isdir(vault):
            nastav_stav("chyba", "nenacházím složku vaultu")
            toast("Nenacházím složku vaultu",
                  "V menu zvol „Zvolit složku vaultu…“ a vyber ji znovu.")
            return
        usb = S["usb"] or najdi_usb()
        if usb is None:
            toast("USB disk není připojený",
                  "Zasuň spárovaný USB disk, synchronizace se spustí sama.")
            return

        nastav_stav("sync")
        zacatek = time.time()
        cil = os.path.join(usb, SLOZKA_ZALOHY)
        ignorovat = set(CFG.get("ignorovat", []))

        if CFG.get("rezim") == "obousmerny":
            vysledek = sync_obousmerny(
                vault, cil, os.path.join(usb, SOUBOR_SNIMKU), ignorovat)
            zprava = zprava_obousmerna(vysledek, time.time() - zacatek)
            if vysledek["konflikty"]:
                toast("Pozor, konflikt poznámek",
                      "Stejná poznámka byla změněná na PC i na USB. Obě verze "
                      "jsou uložené — hledej soubory „(konflikt z USB)“.")
        else:
            zkopirovano, smazano = sync_jednosmerny(vault, cil, ignorovat)
            zprava = zprava_jednosmerna(zkopirovano, smazano,
                                        time.time() - zacatek)

        CFG["posledni_sync"] = datetime.now().isoformat(timespec="seconds")
        uloz_config()
        nastav_stav("hotovo")
        toast("Synchronizace dokončena", zprava)
        time.sleep(1.5)  # zelená fajfka chvíli svítí…
        if S["stav"] == "hotovo":
            nastav_stav("ok")  # …a pak zpět na „vše v pořádku"
    except OSError as chyba:
        loguj(traceback.format_exc())
        if getattr(chyba, "errno", None) == 28:
            text = "Na USB disku není dost místa. Uvolni místo a zkus to znovu."
            kratce = "USB disk je plný"
        elif usb and not os.path.isdir(usb):
            text = ("USB disk byl odpojen uprostřed synchronizace. Zasuň ho "
                    "znovu — synchronizace se spustí sama a dokončí se.")
            kratce = "USB odpojeno při synchronizaci"
        else:
            text = f"{chyba.strerror or chyba}. Zkus synchronizaci spustit znovu."
            kratce = "chyba při synchronizaci"
        nastav_stav("chyba", kratce)
        toast("Synchronizace selhala", text)
    except Exception:
        loguj(traceback.format_exc())
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
    usb = S["usb"] or najdi_usb()
    if usb and os.path.isdir(os.path.join(usb, SLOZKA_ZALOHY)):
        os.startfile(os.path.join(usb, SLOZKA_ZALOHY))
    elif usb:
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
    S["usb"] = koren
    nastav_stav("ok")
    akce_sync()


def autostart_zapnuty(_polozka=None):
    return os.path.isfile(CESTA_AUTOSTART)


def akce_autostart(ikona=None, _polozka=None):
    if autostart_zapnuty():
        try:
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

        disk = najdi_usb()
        if disk and S["usb"] is None:
            S["usb"] = disk
            if not S["vault_chybi"]:
                nastav_stav("ok")
                akce_sync()  # sync při zasunutí
        elif disk is None and S["usb"] is not None:
            S["usb"] = None
            if S["stav"] not in ("sync", "chyba"):
                nastav_stav("ceka")
        time.sleep(max(2, int(CFG.get("interval_kontroly_s", 5))))


def animace():
    """Za letu holub mává křídly — dva snímky, ~0,6 s na cyklus."""
    horni = False
    while S["bezi"]:
        if S["stav"] == "sync" and IKONA["obj"]:
            horni = not horni
            try:
                IKONA["obj"].icon = IKONY["let1" if horni else "let2"]
            except Exception:
                pass
        time.sleep(0.3)

# ---------------------------------------------------------------------------
# Menu a start
# ---------------------------------------------------------------------------


def postav_menu():
    return pystray.Menu(
        pystray.MenuItem("Holub", None, enabled=False),
        pystray.MenuItem(stavovy_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🔄 Synchronizovat teď", akce_sync, default=True),
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
    if not CFG.get("vault"):
        toast("Ahoj, tady Holub 🕊️",
              "Budu ti zálohovat poznámky na USB. Nejdřív mi ukaž složku vaultu.")
        akce_zvolit_vault()


def main():
    # jen jedna instance naráz
    ctypes.windll.kernel32.CreateMutexW(None, False, "Holub-USB-sync")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        zajisti_toast_ikonu()
        toast("Holub už běží", "Ikonu najdeš v liště u hodin.")
        return
    nacti_config()
    zajisti_toast_ikonu()
    IKONA["obj"] = pystray.Icon("Holub", IKONY["ceka"], "Holub", postav_menu())
    IKONA["obj"].run(setup=po_startu)


if __name__ == "__main__":
    main()

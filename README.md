# Holub 🕊️

Malá Windows appka v oznamovací oblasti (ikona u hodin), která zálohuje
Obsidian vault na USB disk. Poštovní holub — nosí poznámky.

## Co umí

- Pozná spárovaný USB disk podle skrytého souboru `.holub-usb` — funguje,
  i když disk dostane jiné písmeno (E:, F:…).
- Synchronizuje **při zasunutí USB** a ručně z menu (pravé tlačítko na ikonu,
  dvojklik = synchronizovat teď).
- **Jednosměrný režim (PC → USB)** — výchozí. USB je přesné zrcadlo vaultu;
  na PC appka v tomto režimu nikdy nezapisuje.
- **Obousměrný režim (PC ⇄ USB)** — pro editaci na jiném počítači. Novější
  verze vyhrává; konflikt se nikdy nepřepisuje potichu, obě verze se uloží
  (`poznamka (konflikt z USB).md`).
- Pixel-art holub hlásí stav: šedý spí (čeká na USB), letí s poznámkou
  (synchronizuje), červený odznak (chyba), zelená fajfka (hotovo).
- Windows oznámení po dokončení / při chybě, volitelné spouštění se systémem.

## Instalace a spuštění

```
py -m pip install -r requirements.txt
pythonw holub.py
```

Při prvním spuštění se Holub představí a nechá si ukázat složku vaultu.
Pak v menu zvol „Spárovat nový USB disk…“ — a je hotovo.

Celý návrh (rozhodnutí, pixelové mapy, etapy stavby, bezpečnostní pravidla)
je v [plan.md](plan.md).

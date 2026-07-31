# Holub — synchronizace Obsidian vaultu na USB

*Plán vytvořen 2026-07-31 (Claude Fable 5). Tento soubor je zdroj pravdy pro stavbu appky — obsahuje rozhodnutí, design i postup, aby na něm šlo stavět v jakémkoli dalším chatu.*

## Stav stavby (2026-07-31)

**Etapy 1–5 postavené** v `holub.py` (jeden soubor podle plánu). Sync logika prošla 24 testy na cvičném vaultu — včetně bezpečnostních pravidel (jednosměrný režim na PC nic nezapisuje, konflikty se nepřepisují, úprava poráží smazání). Appka nastartovala v liště bez pádu (smoke test s `--config` na cvičný config).

**Zbývá (etapa 6 — ruční ověření Tomášem):** spárovat opravdový USB disk, nechat proběhnout první sync na cvičné kopii vaultu, pak teprve nastavit ostrý vault. Otevřená zůstává otázka 2 (časovač při trvale zastrčeném USB) — appka zatím syncuje při zasunutí, při startu a ručně.

Odchylky od plánu: autostart se dělá malým souborem `Holub.pyw` ve složce Po spuštění (ne zástupcem `.lnk` — výsledek stejný, jednodušší na výrobu); navíc přibyl přepínač `--config` pro testování a zámek proti dvojímu spuštění appky.

**Časovač (HOTOVO 2026-07-31, Tomášovo zadání — řeší otázku 2):** podmenu „⏰ Automatická synchronizace" — vypnutá (výchozí) / každých 15/30/60 minut / libovolný interval / každý den v HH:MM. Vlastní hodnoty se zadávají tmavým dialogem (běží ve vlákně okna Přehledu). Chování: interval se počítá od posledního úspěšného syncu (jedno jakého — ručního, po zasunutí, z časovače); bez připojeného USB časovač mlčky čeká, žádné chybové toasty; denní čas, který proběhl před spuštěním appky, se nedohání (start obstará hlídač USB). Config: `casovac`, `casovac_minuty`, `casovac_denne`. Testy rozšířeny na 32.

**Etapa 7 — okno „Přehled" (HOTOVO 2026-07-31, Tomášovo zadání nad rámec původního plánu):** tmavé tkinter okno (vlastní trvalé vlákno + fronta příkazů; tmavá horní lišta přes DWM atribut 20). Obsah: historie synchronizací (nový soubor `holub-historie.json`, drží posledních 200 záznamů), seznam konfliktních kopií s tlačítky Otevřít kopii / Otevřít původní / Smazat kopii, a „Zkontrolovat změny" = synchronizace nanečisto (parametr `naostro=False` v obou sync funkcích — jen počítá, ničeho se nedotkne). Dvojklik na ikonu teď otevírá Přehled (dřív spouštěl sync). Testy rozšířeny na 30.

## Co to je

Malá Python appka v oznamovací oblasti Windows (ikona u hodin). Hlídá připojení spárovaného USB disku a synchronizuje na něj Obsidian vault. Maskot a pracovní název: **Holub** (poštovní holub — nosí poznámky). Žádné velké okno — celá appka je: ikona v liště, menu na pravé tlačítko, oznámení (toasty).

**Proč vlastní appka:** Tomáš nechce platit Obsidian Sync (vault je moc velký) a FreeFileSync se mu nelíbí vizuálně. PowerShell skript zamítnut jako moc komplikovaný.

## Rozhodnutí (schváleno Tomášem)

- **Oba režimy synchronizace**, přepínatelné v menu:
  - **Jednosměrný (PC → USB)** — výchozí. USB je přesné zrcadlo vaultu: změněné poznámky se zkopírují, co je smazané na PC, smaže se i na USB. Appka v tomto režimu **nikdy nezapisuje na PC**.
  - **Obousměrný (PC ⇄ USB)** — pro editaci na jiném počítači. U každé poznámky vyhrává novější verze. Na USB se drží malý „snímek posledního syncu" (JSON), aby appka rozeznala „smazáno na PC" od „nové na USB". Konflikt (stejná poznámka změněná na obou stranách) se **nikdy nepřepisuje potichu** — uloží se obě verze, druhá jako `nazev (konflikt z USB).md`.
- **Párování přes soubor, ne písmeno disku:** menu „Spárovat nový USB disk…" zapíše na USB skrytý soubor `.holub-usb`. Appka pak disk pozná, i když dostane jiné písmeno (E:, F:…).
- **Kdy se syncuje:** při zasunutí USB + ručně z menu. Kontrola „je USB připojené?" běží každých ~5 s, ale je to jen dotaz do Windows na seznam disků — nic nečte, nekopíruje, do vaultu nesahá. Opotřebení USB je nepodstatné: flash se opotřebovává jen zápisem, a appka porovnává napřed (datum úpravy + velikost) — beze změn se nezapíše ani bajt.
- **Ignorovat** `.obsidian/workspace.json` a `workspace-mobile.json` (mění se každým kliknutím, syncovat je = zbytečné konflikty).
- Sync se nespustí dvakrát naráz (zámek). Chyba (plný disk, vytažení uprostřed) = červená ikona + toast s vysvětlením.
- UI texty česky.

## Otevřené otázky (zeptat se Tomáše před stavbou / během ní)

1. **Cesta k Obsidian vaultu** — zatím neznámá. Není blokující: nastaví se dialogem při prvním spuštění (viz „Nastavení složky vaultu"), ale znát ji dřív se hodí na testování.
2. **Bude USB trvale zastrčené v PC?** Pokud ano, přidat časovač (např. sync jednou za 30 min, jen když se něco změnilo). Pokud se zastrkává jen na zálohu, časovač netřeba. → **VYŘEŠENO 2026-07-31: Tomáš si časovač vyžádal** (denně v X + každých X minut), viz „Časovač" ve Stavu stavby.
3. Název „Holub" je pracovní — Tomáš může přejmenovat.

## Technologie

Jeden Python skript v této složce (`C:\Users\tomas\Desktop\USB sync`). Knihovny:

| Knihovna | K čemu |
|---|---|
| `pystray` | ikona v liště + menu |
| `Pillow` | vykreslení pixel-art ikony (z pixelových map níže) |
| `winotify` | Windows oznámení (toasty) |

Žádný PowerShell. Autostart = zástupce ve složce Po spuštění (`shell:startup`) spouštějící skript přes `pythonw.exe` (bez černého okna).

### Soubory

```
USB sync/
  holub.py        hlavní skript
  config.json     nastavení
  plan.md         tento plán
Na USB:
  .holub-usb            párovací značka (skrytý soubor)
  holub-snapshot.json   paměť posledního syncu (jen obousměrný režim)
  Vault/                kopie vaultu
```

### config.json (příklad)

```json
{
  "vault": "C:\\cesta\\k\\vaultu",
  "rezim": "jednosmerny",
  "interval_kontroly_s": 5,
  "ignorovat": [".obsidian/workspace.json", ".obsidian/workspace-mobile.json"]
}
```

**Nastavení složky vaultu:** Tomáš nikdy needituje `config.json` ručně. Cesta se nastavuje v menu položkou „Zvolit složku vaultu…" — otevře se běžný Windows dialog pro výběr složky (tkinter `askdirectory`, je v Pythonu vestavěné, žádná knihovna navíc) a appka si ji sama zapíše do configu. **První spuštění:** když v configu žádný vault není, appka se představí toastem a rovnou dialog otevře. Když nastavená složka přestane existovat (přejmenování, přesun), ikona zčervená a toast vyzve k novému výběru — appka nikdy nesyncuje „naslepo".

## Design (schválený náhled z 2026-07-30)

Styl: pixel art, Windows 11 dark vzhled menu a toastů, písmo Segoe UI.

### Stavy ikony (16×16 px)

| Stav | Vzhled |
|---|---|
| Čeká na USB | šedý spící holub |
| Vše v pořádku | barevný holub stojí |
| Synchronizuje | holub letí s poznámkou v zobáku — animace 2 snímky (křídla nahoře/dole), ~0,6 s cyklus |
| Chyba | stojící holub + červený odznak s vykřičníkem vpravo nahoře |
| Hotovo | krátce zelená fajfka, pak zpět na „vše v pořádku" (+ toast) |

### Pixelové mapy (16×16, `.` = průhledná)

Paleta: `B` tělo `#8fa3bf` · `D` tmavá (křídlo/ocas) `#5f7396` · `W` světlá skvrna `#cdd9e8` · `K` zobák `#e59a3c` · `E` oko `#23272e` · `G` zelený lesk krku `#3fae7a` · `L` nohy `#d96a45` · `N` poznámka `#f7f2e2` · `R` odznak `#d94f4f` · `X` bílá `#ffffff`

Šedá varianta (čekání): `B #a6a6a6, D #8b8b8b, W #c8c8c8, K #b3b3b3, E #7a7a7a, G #9c9c9c, L #9e9e9e`

**STOJÍCÍ (základ):**
```
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
```

**LET — snímek 1 (křídla nahoru, s poznámkou):**
```
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
(zbytek prázdný)
```

**LET — snímek 2 (křídla dolů):**
```
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
(zbytek prázdný)
```

**CHYBA** = STOJÍCÍ + odznak v řádcích 0–4 (přepsat pravou část řádků):
```
řádek 0: ............RRR.
řádek 1: ....BB.....RRXRR
řádek 2: ...BBBB....RRXRR
řádek 3: ..KBEBB....RRRRR
řádek 4: ...BBBB.....RXR.
```

### Menu (pravé tlačítko)

```
[ikona] Holub
        vše synchronizováno · dnes 14:32     ← stavový řádek, neklikací
──────────────────────────────
🔄 Synchronizovat teď
⇄  Režim synchronizace          ▸  → podmenu: ✓ Jednosměrný (PC → USB)
                                              Obousměrný (PC ⇄ USB)
📁 Otevřít zálohu na USB
📂 Zvolit složku vaultu…              ← otevře Windows dialog pro výběr složky
🔌 Spárovat nový USB disk…
✓  Spouštět se systémem Windows      ← zaškrtávací
──────────────────────────────
✕  Ukončit
```

### Toast (oznámení)

Malá tmavá karta vpravo dole: hlavička „Holub · právě teď", ikona holuba 32 px, titulek **„Synchronizace dokončena"**, podtitulek „12 poznámek zkopírováno na USB · 3 s". Při chybě totéž s vysvětlením, co se stalo a co s tím.

## Etapy stavby

| Etapa | Co se postaví | Doporučený model |
|---|---|---|
| 1 | Kostra: ikona v liště (Pillow z pixelových map), menu, Ukončit | Sonnet 5 |
| 2 | Párování USB (`.holub-usb`) + detekce připojení | Sonnet 5 |
| 3 | Jednosměrný sync (zrcadlo) + toasty + animace letu | Sonnet 5 |
| 4 | Obousměrný režim: snapshot, mazání vs. nové soubory, konfliktní kopie | **Opus 5** — nejzapeklitější logika, chyba tu znamená ztracené poznámky |
| 5 | Autostart, přepínač režimů v menu, doladění | Sonnet 5 |
| 6 | **Test na cvičném vaultu** (kopie pár poznámek) — teprve pak napojit na ostrý vault | Sonnet 5 |

## Bezpečnostní pravidla (neporušovat)

1. Jednosměrný režim **nikdy** nezapisuje ani nemaže na PC.
2. Obousměrný režim **nikdy** potichu nepřepisuje konflikt — vždy konfliktní kopie.
3. Nikdy nesyncovat bez platné párovací značky na disku (aby se nezapisovalo na cizí USB).
4. První ostrý běh vždy nejdřív nanečisto na cvičném vaultu (etapa 6).

# WaterFall — jak s tím pracovat

Sonda nRF52840 měří jen **energii (RSSI)** v pásmu 2400–2500 MHz: čas, frekvenci, sílu. **Nečte pakety VUSION** a **nevysílá**. Špička na 2431 MHz znamená „tam něco pípá“, ne „tohle je štítek“.

Stejný návod je ve webu v záložce **Nápověda**.

## Jak číst obrazovku

1. **Spektrum / waterfall** — energie z donglu. Přes to se kreslí **sítě z Wi-Fi na Raspberry** (SSID + kanál). Dongle pořád nezná SSID; Pi ho přečetlo ze beaconu a WaterFall to jen položí na stejnou MHz.
2. **Easy START** — SCAN + zapamatuje pokoj. **POWER ON** hledá novou úzkou špičku *mimo* ty Wi-Fi kanály.
3. **Pro** — capture, WATCH, „Co ruší pásmo“, BLE/monitor.

## První sezení

1. Sonda svítí zeleně. Nech běžet **SCAN** (po připojení se spouští sám).
2. Kde jsou ve waterfallu světlé svislé pruhy, tam se něco opakuje.
3. U rušiců nech **Logovat nové**. Klikni na MHz i potom, co z živého seznamu zmizela.
4. **Zapsat** → záložka **Špičky** → pojmenuj → volitelně **Držet (WATCH)**.
5. Chceš deník v čase? Název session → **START** capture → **STOP**. V Capture klikni na řádek: vpravo je, jestli známe zařízení, nebo jen energii.

## SCAN vs WATCH

| Režim | K čemu |
|---|---|
| **SCAN** | Přehled celého pásma (~2× za sekundu). „Kde to pípá?“ |
| **WATCH** | Jedna MHz, časování burstů (NFC, relé). SCAN stojí. |

Sonda má jedno rádio — nejde obojí naráz.

## Easy a Pro

**Easy** (výchozí): SCAN, capture, WATCH, relé štítku, Spektrum / Špičky / Capture.  
**Pro**: krok sweepu, BLE/Wi-Fi, CSV, PCAP, katalog zařízení.

## Zkouška štítku (relé)

Štítek visí na relé. Tím ho vypneš a zapneš a čekáš špičku po náběhu. Sonda vidí jen energii 2400–2500 MHz. Když rádio jede na 868 MHz, tady nic neuvidíš.

Platí jen to, co se **objeví s POWER ON** a **zmizí s POWER OFF**. Samovolné 2431 / 2455 / 2470 je skoro jistě pokoj.

1. Dongle 20–40 cm od štítku. **Logovat nové** zapnuté. Session `stitok-rele` → **START** capture.
2. **POWER OFF**, 10–15 s klid. Volitelně **Zapamatovat klid**.
3. **SCAN** + **POWER ON**. Odznak Štítek ON, v capture marker `POWER_ON`.
4. 2–8 s po zapnutí hledej úzkou novou čáru (1–3 MHz) ve waterfallu nebo novou položku v logu rušiců.
5. Klik → **Zapsat** → pojmenuj → **WATCH** na té MHz.
6. S WATCH pětkrát: OFF → 10 s → ON. Burst má sedět na zapnutí. V Capture střídání `POWER_ON` / `POWER_OFF`.
7. Kontrola: totéž bez štítku na relé. Burst se nesmí opakovat.

**Ano:** stejná MHz v 5/5, bez štítku nic, není to ~20 MHz Wi-Fi.  
**Ne:** skáče to i při OFF, nebo to sedí na Wi-Fi v místnosti → 2,4 GHz z této sondy nepotvrdíš; další je sub-GHz.

## Co to zatím není

Není to SDR. Z RSSI nepozná MAC štítku. Do éteru nic nepošle. Nejdřív vidět a uložit, potom teprve mluvit.

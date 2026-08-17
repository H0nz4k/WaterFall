# WaterFall — jak s tím pracovat

Sonda nRF52840 měří jen **energii (RSSI)** v pásmu 2400–2500 MHz: čas, frekvenci, sílu. **Nečte pakety VUSION** a **nevysílá**. Špička na 2431 MHz znamená „tam něco pípá“, ne „tohle je štítek“.

Stejný návod je ve webu v záložce **Nápověda**.

## Jak číst obrazovku

1. **Spektrum** — čára přes pásmo. Výška = síla. Žlutá čárkovaná čára je šum (median).
2. **Waterfall** — čas dolů, frekvence doprava, světlejší = silnější. Krátké čárky jsou krátké bursty.
3. **Co ruší pásmo** — odhad z energie v aktuálním sweepu. Skáče, protože signál často trvá kratší dobu než jeden průchod pásmem.
4. **Logovat nové** (zapnuté) — když rušič zmizí, zůstane v logu. Klikni, až se trefíš. Karta: **Zapsat** do Špiček, **WATCH sem**, nebo Capture.
5. **WATCH bursty** ožijí až spustíš WATCH na jedné frekvenci. SCAN mezitím stojí.

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

**Easy** (výchozí): SCAN, capture, WATCH, Spektrum / Špičky / Capture.  
**Pro**: krok sweepu, BLE/Wi-Fi, CSV, relé, PCAP, katalog zařízení.

## Co to zatím není

Není to SDR. Z RSSI nepozná MAC štítku. Do éteru nic nepošle. Nejdřív vidět a uložit, potom teprve mluvit.

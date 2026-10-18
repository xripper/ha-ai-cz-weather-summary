# CZ Weather Summary

Integrace pro Home Assistant (HACS), která pro 1–3 místa stáhne dnešní předpověď počasí od **ČHMÚ** a nechá z ní **OpenAI** model napsat krátký český souhrn včetně doporučení, co si vzít na sebe. Výsledek je senzor, který se hodí na domácí dashboard.

## Odkud se berou data

- **Slovní předpověď meteorologů ČHMÚ pro kraj** – [opendata.chmi.cz](https://opendata.chmi.cz/meteorology/weather/forecast/now/), předpověď na dnešek a na noc. Kraj se určí automaticky podle souřadnic (případně jde nastavit ručně).
- **Hodinová data modelu ALADIN (ČHMÚ)** pro přesné souřadnice – přes [Open-Meteo](https://open-meteo.com/en/docs/chmi-api). Z dat se spočítají teploty, pocitová teplota, srážky, vítr a oblačnost po částech dne.
- Nebezpečné jevy, pokud je ČHMÚ uvádí v krajské předpovědi.

Místa mimo ČR dostanou jen data z modelu ALADIN.

## Instalace

1. HACS → Integrace → menu ⋮ → *Vlastní repozitáře* → `https://github.com/xripper/ha-ai-cz-weather-summary`, kategorie *Integrace*.
2. Nainstaluj **CZ Weather Summary** a restartuj Home Assistant.
3. *Nastavení → Zařízení a služby → Přidat integraci → CZ Weather Summary.*
4. Zadej OpenAI API klíč, vyber model, čas denního generování (výchozí 06:00) a 1–3 místa (název + poloha na mapě; první místo je předvyplněné podle domova).

V *Možnostech* integrace můžeš později změnit model, čas, místa, kraj ČHMÚ pro každé místo (pokud ho automatika u hranice kraje určí špatně) a **systémový prompt** (styl a obsah textu; smazáním se vrátí výchozí). Místo 2 nebo 3 odebereš smazáním jeho názvu. Po uložení se souhrn hned vygeneruje znovu.

Při výpadku ČHMÚ nebo OpenAI zůstane zobrazen předchozí souhrn a integrace to zkusí znovu každých 30 minut (max. 6×).

## Senzor

`sensor.souhrn_pocasi_souhrn_pocasi` (název se může lišit podle jazyka):

| | |
|---|---|
| stav | jedna krátká věta (max 255 znaků) |
| `summary` | celý souhrn |
| `clothing` | doporučení, co si vzít na sebe |
| `locations` | data jednotlivých míst (kraj, min/max teplota, srážky, vítr, výstrahy) |
| `generated_at`, `model` | kdy a jakým modelem byl text vytvořen |
| `last_error` | poslední chyba (při chybě zůstává zobrazen předchozí souhrn) |

Souhrn se generuje jednou denně v nastavený čas. Po restartu HA se znovu negeneruje, pokud je platný; pokud byl HA v naplánovaný čas vypnutý, vygeneruje se po startu.

## Služba

`cz_weather_summary.refresh` – znovu stáhne předpověď a vygeneruje souhrn (volitelně jen pro jednu položku `entry_id`). Hodí se do automatizací, např. při odchodu z domu.

## Karta na dashboard

```yaml
type: markdown
content: >
  ### {{ states('sensor.souhrn_pocasi_souhrn_pocasi') }}

  {{ state_attr('sensor.souhrn_pocasi_souhrn_pocasi', 'summary') }}

  **Co na sebe:** {{ state_attr('sensor.souhrn_pocasi_souhrn_pocasi', 'clothing') }}
```

## Cena

Jedno generování = jeden požadavek na OpenAI (zhruba 1–2 tisíce vstupních tokenů). U „mini“ modelů to vychází na zlomky haléřů denně.

## Vývoj

```bash
pip install -r requirements_test.txt
pytest
python -m script.smoke            # živá data z ČHMÚ a Open-Meteo
OPENAI_API_KEY=sk-... python -m script.smoke   # včetně souhrnu z OpenAI
```

Testy vyžadují Linux nebo macOS (Home Assistant nejde spustit na Windows, použij WSL).
Polygony krajů (`regions.json`) se generují skriptem `script/build_regions.py`.

## Licence a zdroje

MIT. Data: © ČHMÚ, otevřená data ([opendata.chmi.cz](https://opendata.chmi.cz)); model ALADIN zprostředkovaný službou [Open-Meteo](https://open-meteo.com) (CC BY 4.0, zdarma pro nekomerční použití).

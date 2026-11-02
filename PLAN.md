# ha-nemlig — plan for MVP

Custom integration (HACS) til Home Assistant, domæne `nemlig`. Bygger på API-viden fra
[DruonDK/nemlig_cli](https://github.com/DruonDK/nemlig_cli) (`nemlig_api.md` er kilden til
endpoints). Mål-HA: 2026.10.x.

**MVP = levering + kurv-sensor + få actions.** To-do-liste, tilbud, forbrugsstatistik,
"løber tør"-forudsigelse, slot-booking og madplan kommer bagefter.

---

## Arkitekturvalg

| Valg | Beslutning | Hvorfor |
|---|---|---|
| Repo | Nyt repo `DruonDK/ha-nemlig`, `custom_components/nemlig/` | HACS kræver én integration pr. repo; nemlig_cli forbliver CLI/MCP + API-discovery |
| Klient | Egen **async** klient `api.py` (aiohttp), kun de endpoints MVP'en bruger | `nemlig_cli.py` er synkron `requests` med spinner/CLI-kode; et lille async-modul er renere end executor-wrapping. Kan senere flyttes ud som PyPI-lib |
| HTTP-session | Egen `async_create_clientsession` (egen cookie jar) | Login sætter cookies (basket-key m.m.), som ikke må blandes med HA's delte session |
| Auth | Token-cache ~4,5 min; ved 401/403 ét re-login + retry; `ConfigEntryAuthFailed` → reauth-flow | Samme mønster som `server.py` |
| Throttle | `asyncio`-lås, maks ~2 req/s, delt af alle kald | API'et er udokumenteret og ikke vores |
| MCP | Bruges **ikke** af integrationen | HA's egen Assist/MCP eksponerer entities og actions bagefter |

## Fase 0 — API-verifikation (før kode)

Med Chrome DevTools-MCP'en i nemlig_cli (i subagent, jf. CLAUDE.md der):

1. **Ordrens livscyklus i `GET /webapi/Order/DeliverySpot`**: hvilke `State`-værdier og
   `Progress`-værdier optræder fra bestilt → kan rettes → pakkes → undervejs → leveret?
   Hvad returneres uden kommende ordre? Kun dokumenteret én gang (`"Reorder"`).
   Kræver en capture på en leveringsdag.
2. **Kommende ordrer i `GetBasicOrderHistory`**: ser vi `Status` 1/2 og `IsEditable: true`
   for en ikke-leveret ordre? Flere samtidige ordrer?
3. **Live-tracking**: viser appen/sitet et snævrere ETA eller "chaufføren er på vej"? Find
   endpointet, hvis det findes.
4. **Login-sideeffekt**: login sender `DoMerge: true` / `SaveExistingBasket: false`. Gentagne
   logins fra HA må ikke ødelægge familiens kurv i appen. Test med varer i kurven fra
   appen, og log ind via klienten.

Resultater dokumenteres i `nemlig_api.md` (anonymiseret).

## Fase 1 — Skelet og klient

- `manifest.json` (`iot_class: cloud_polling`, `config_flow: true`), `hacs.json`, `const.py`.
- `api.py`: `login`, `get_delivery_spot`, `get_order_history`, `get_order_details`,
  `get_basket`, `set_basket_quantity`, `search_products`, `get_shopping_lists`,
  `add_shopping_list_to_basket`, `get_page_settings`. Egne exceptions:
  `NemligAuthError`, `NemligApiError`.
- **Config flow**: email + adgangskode, valideres ved login; unik id = email.
  **Reauth flow**. Options: poll-interval og søge-fallback (se fase 3).
- `diagnostics.py` med redaction af email, navn, adresse og telefon.

## Fase 2 — Levering

**`NemligDeliveryCoordinator`**: `DeliverySpot` + `GetBasicOrderHistory(take=5)`.
Adaptivt interval: 30 min normalt, 5 min på leveringsdagen fra 1 time før vinduet til
leveret.

| Entity | Type | Indhold |
|---|---|---|
| `sensor.nemlig_naeste_levering` | timestamp | Vinduets start; attributter: `slut`, `ordrenummer`, `total`, `kan_rettes`, `uden_opsyn` |
| `sensor.nemlig_rettefrist` | timestamp | `EditDeadline`; `unknown` når ingen ordre kan rettes |
| `sensor.nemlig_ordrestatus` | enum | `ingen`, `bestilt`, `pakkes`, `undervejs`, `leveret` (endelige værdier fra fase 0) |
| `binary_sensor.nemlig_levering_i_dag` | on/off | Kommende levering har dato i dag |
| `calendar.nemlig` | calendar | Kommende og seneste leveringer som events (vindue, ordrenr., beløb) |

Event `nemlig_delivered` udløses ved overgang til leveret, så automationer ikke behøver at
følge statusændringer selv.

## Fase 3 — Kurv

**`NemligBasketCoordinator`**: `GetBasket` hvert 10. min + straks efter egne writes.
Kurven på nemlig.com er sandheden; HA ejer ingen kopi og redigeres i appen.

| Entity | Indhold |
|---|---|
| `sensor.nemlig_kurv` | Total i DKK (`device_class: monetary`); attributter `antal_varer`, `linjer` (navn, antal, pris) |

**Produktindeks ("købt før")**: linjer fra de seneste ~15 ordrer
(`GetOrderHistory/{id}` → `ProductNumber`, `ProductName`, `Description`, antal gange købt).
Gemmes med `helpers.storage.Store`, bygges ved opstart og opdateres efter hver levering.
Bruges af `nemlig.add_product` med `query`: fuzzy match vægtet efter købsfrekvens; uden
match over tærsklen fejler actionen med de bedste søgeforslag i stedet for at gætte.

## Fase 4 — Actions

| Action | Bemærkning |
|---|---|
| `nemlig.add_product` | `product_id` eller `query` + `quantity`; `query` matches mod produktindekset (fase 3) |
| `nemlig.add_shopping_list` | Navn (case-insensitive) eller id på en favoritliste |
| `nemlig.search` | Response-action: liste af `{id, navn, pris, enhedspris, tilbud}` til scripts/LLM |

Ingen booking af leveringstid i MVP'en, fordi det koster penge og reservationen udløber
efter ca. 20 min. Checkout findes ikke i API'et; betaling sker i appen.

## Fase 5 — Test og udrulning

- `pytest-homeassistant-custom-component`; JSON-fixtures afledt af `nemlig_api.md`
  (anonymiseret). Dækker config/reauth flow, statusmapping i coordinatoren, produktmatch
  (tærskel, ukendt vare), re-login ved 401.
- CI: hassfest + HACS-validering på GitHub Actions.
- Installér som custom repo i HACS på egen HA; kør en uge med en rigtig levering.
- Dashboard: følg Overview v2-principperne (ét hjem pr. oplysning). Gammelt dashboard
  røres ikke.

## Åbne spørgsmål

1. ~~To-do~~ — **besluttet: ude af MVP.** Ingen stemmesatellitter i dag, og en 1:1-kopi af
   kurven giver uklar afkrydsning. Bygges senere som "mangler"-liste (se nedenfor).
2. ~~Søge-fallback~~ — bortfaldet med to-do; `add_product` gætter ikke, den foreslår.
3. ~~Repo~~ — **besluttet: offentligt fra start.** Fixtures og testdata skal derfor være
   anonymiseret fra første commit (kør `/privacy-checker`-logikken fra nemlig_cli før push).

## Efter MVP

**`todo.nemlig_mangler`** (når/hvis der kommer en Assist-satellit): fritekst-ønsker →
integrationen matcher og lægger i kurven → varen krydses af med produktnavnet i
description; umatchede bliver stående åbne; afkrydsede ryddes efter levering.

Tilbud på faste varer (event) · månedsforbrug (`state_class: total`) · "løber snart tør"
· næste ledige/billigste leveringstid · `nemlig.repeat_order` · booking af tid med
bekræftelse · madplan → kurv.

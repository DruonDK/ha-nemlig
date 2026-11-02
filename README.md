# Nemlig.com for Home Assistant

Custom integration for [nemlig.com](https://www.nemlig.com), the Danish online grocery
store. Work in progress — see [PLAN.md](PLAN.md).

The nemlig.com API is undocumented. Endpoints come from
[DruonDK/nemlig_cli](https://github.com/DruonDK/nemlig_cli), whose `nemlig_api.md` is the
reference. The integration is not affiliated with nemlig.com and may break when the site
changes.

## Status

- [x] Async API client, config flow, reauth
- [x] Delivery sensors, calendar and `nemlig_delivered` event
- [ ] Basket sensor and product index
- [ ] Actions: `nemlig.add_product`, `nemlig.add_shopping_list`, `nemlig.search`

## Installation (HACS)

1. HACS → ⋮ → Custom repositories → add `https://github.com/DruonDK/ha-nemlig`
   as an *Integration*.
2. Install **Nemlig.com**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **Nemlig.com**, and log in with your
   nemlig.com email and password.

## Behaviour worth knowing

- Logs in with your nemlig.com credentials and renews its token every ~4.5 minutes.
  Logging in does not change your basket.
- At most ~2 requests per second against nemlig.com.
- The basket is the same one you see in the nemlig.com app; the integration never places
  orders or pays.

## Development

```bash
uv sync --python 3.14
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Test fixtures are anonymised; never commit real names, addresses or order numbers.

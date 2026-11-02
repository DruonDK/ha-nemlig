# Nemlig.com for Home Assistant

Custom integration for [nemlig.com](https://www.nemlig.com), the Danish online grocery
store. It follows your next delivery, shows the live basket and lets automations and
assistants put products in it.

The nemlig.com API is undocumented. Endpoints come from
[DruonDK/nemlig_cli](https://github.com/DruonDK/nemlig_cli), whose `nemlig_api.md` is the
reference. The integration is not affiliated with nemlig.com and may break when the site
changes. Roadmap: [PLAN.md](PLAN.md).

## What it does

- **Delivery tracking** — next delivery window, edit deadline, expected arrival and
  progress while the driver is on the way, order status as an enum, a "delivery today"
  binary sensor and a calendar of recent and upcoming deliveries.
- **`nemlig_delivered` event** — fired once when an order turns delivered, so automations
  don't have to watch status changes themselves.
- **Basket sensor** — the live basket shared with the nemlig.com app: products total,
  item count, delivery price, minimum order and every line as attributes.
- **Actions** — `nemlig.add_product` (by product ID, or by free text matched against what
  the household usually buys), `nemlig.add_shopping_list` and `nemlig.search`.
- **"Bought before" product index** — built from the last 15 delivered orders, kept in HA
  storage, refreshed after each delivery. It is what turns "mælk" into the right product.

It never places orders, pays or books delivery slots. Checkout stays in the nemlig.com app.

## Installation (HACS)

1. HACS → ⋮ → Custom repositories → add `https://github.com/DruonDK/ha-nemlig`
   as an *Integration*.
2. Install **Nemlig.com**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **Nemlig.com**, and log in with your
   nemlig.com email and password.

One config entry per nemlig.com account. If nemlig.com stops accepting the password, the
entry asks for a new one (reauth flow). Requires Home Assistant 2026.10 or newer.

## Entities

All entities belong to one **Nemlig** device per account. Entity IDs below assume the
default naming.

| Entity | Type | Value | Attributes |
|---|---|---|---|
| `sensor.nemlig_next_delivery` | timestamp | Start of the upcoming order's delivery window | `window_end`, `order_number`, `total` |
| `sensor.nemlig_edit_deadline` | timestamp | Until when the upcoming order can still be changed on nemlig.com; `unknown` once passed | |
| `sensor.nemlig_expected_arrival` | timestamp | Nemlig's current ETA for the driver, when it reports one | `window_start`, `window_end` (the ETA range) |
| `sensor.nemlig_delivery_progress` | % | Delivery progress reported by nemlig.com on the day | |
| `sensor.nemlig_order_status` | enum | `none`, `editable`, `processing`, `ready_for_delivery`, `out_for_delivery`, `delivered` | `nemlig_state` (nemlig's raw DeliverySpot state) |
| `binary_sensor.nemlig_delivery_today` | on/off | On when an order's delivery window is today, also after it has arrived | |
| `calendar.nemlig_deliveries` | calendar | One event per recent or upcoming delivery: the window as start/end, order number and total in the description | |
| `sensor.nemlig_basket` | DKK (monetary) | Products total of the live basket | `item_count`, `total_price` (incl. delivery), `delivery_price`, `minimum_order_total`, `missing_to_minimum`, `lines` (product_id, name, quantity, price) |

### Order status

The status combines the order history with nemlig's `DeliverySpot`:

| Status | Meaning |
|---|---|
| `none` | No upcoming order (and no delivery today) |
| `editable` | Ordered; can still be changed until the edit deadline |
| `processing` | Deadline passed; being picked and packed |
| `ready_for_delivery` | Packed, waiting for the driver |
| `out_for_delivery` | Driver on the way; expected arrival and progress are live |
| `delivered` | Dropped off. Stays `delivered` for the rest of the delivery day |

### `nemlig_delivered` event

Fired on the event bus once per delivery, when the status turns `delivered`:

```yaml
event_type: nemlig_delivered
data:
  config_entry_id: "…"
  order_id: 89485948
  order_number: "1065925710"
  total: 1301.41
```

```yaml
automation:
  - alias: Groceries arrived
    triggers:
      - trigger: event
        event_type: nemlig_delivered
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "Nemlig delivered order {{ trigger.event.data.order_number }}"
```

## Actions

All actions take an optional `config_entry_id`; it is only needed with more than one
nemlig.com account.

### `nemlig.add_product`

Adds a product to the live basket. Give either `product_id` or `query`.

| Field | Description |
|---|---|
| `product_id` | The nemlig.com product number, e.g. `701025` |
| `query` | Free text such as `mælk`. Matched against products bought before, not the whole catalogue |
| `quantity` | How many to **add** to what is already in the basket (default 1, max 50) |

With `query`, the product index picks a product only when the choice is clear: an exact
name match, a single candidate, or a clear favourite (bought at least twice and at least
twice as often as the runner-up). Otherwise the action fails and names the candidates, or,
when nothing matches, the top catalogue search results, so you can call again with a
`product_id`. Danish word forms are handled: `æble` matches "Æbler", `brød` matches
"Rugbrød", but `ost` does not match "Frost".

```yaml
action: nemlig.add_product
data:
  query: mælk
  quantity: 2
```

Response (`response_variable`): `status` (`added`, `ambiguous` or `not_found`), the
product's `product_id`, `name` and resulting `quantity`, plus `basket_total`,
`basket_items` and `missing_to_minimum`. `ambiguous` returns `candidates`, `not_found`
returns `suggestions`.

### `nemlig.add_shopping_list`

Adds every product of a saved nemlig.com shopping list (favoritter) to the basket.

| Field | Description |
|---|---|
| `list` | The list's name (case-insensitive) or ID |

Response: `status`, `list`, `basket_total`, `basket_items`, `missing_to_minimum`.

### `nemlig.search`

Searches the nemlig.com catalogue; response only, nothing is changed.

| Field | Description |
|---|---|
| `query` | What to search for |
| `limit` | Maximum number of products (default 10, max 50) |

Returns `products`, each with `product_id`, `name`, `brand`, `description`, `price`,
`unit_price`, `unit_price_label`, `on_offer`, `offer_price`, `offer_min_quantity` and
`available`. Useful from scripts and from an LLM-based assistant that has the action
exposed.

```yaml
action: nemlig.search
data:
  query: kaffe
  limit: 5
response_variable: found
```

## Behaviour worth knowing

- **Login** uses your nemlig.com credentials and renews the token every ~4.5 minutes;
  on a rejected request it logs in once more and retries, and asks for a new password
  only when nemlig.com keeps refusing. Logging in does not change your basket.
- **Polling**: delivery data every 30 minutes, every 5 minutes from one hour before the
  delivery window until one hour after it; the basket every 10 minutes and immediately
  after an action changes it.
- **Rate limit**: at most ~2 requests per second against nemlig.com, shared by all calls.
- **Times**: nemlig.com mixes UTC and Danish local time; everything is converted, so the
  entities are right whatever time zone Home Assistant runs in.
- **The basket is real.** It is the same one you see in the nemlig.com app, and
  `AddToBasket` on nemlig.com sets absolute quantities. The action reads the basket
  first and adds to the current quantity, so calling it twice adds twice.
- **Diagnostics** (Settings → Devices & services → Nemlig.com → Download diagnostics)
  include the delivery and basket data and the product index, with the email, password
  and account identifiers redacted.

## Not included (yet)

Booking a delivery slot, checkout, a to-do list mirror of the basket, offers and
consumption statistics. See [PLAN.md](PLAN.md) for what comes after the MVP.

## Development

```bash
uv sync --python 3.14
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

CI runs the tests, ruff, hassfest and HACS validation on every push. Test fixtures are
anonymised; never commit real names, addresses or order numbers.

## License

MIT

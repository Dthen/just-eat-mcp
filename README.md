# just-eat-mcp

MCP server for Just Eat UK. No auth. Pure stdlib.
Speaks the 2026-07-28 stateless-era protocol: no `initialize` handshake (rejected with `-32601`), discovery via `server/discover`.

## Tools

- `search_restaurants` — by postcode
- `search_by_location` — by lat/lon
- `get_curated_lists` — curated collections
- `get_cuisines` — cuisine breakdown
- `get_menu` — full categorized menu with customizations
- `get_restaurant_info` — description, hours, cuisines

## Use

```bash
hermes mcp add just-eat --command /usr/bin/python3 --args server.py
```

## Run

```bash
/usr/bin/python3 server.py
```

Production invocation is the system python (`/usr/bin/python3`, verified 3.12.3 in the live
Hermes config) — the server is stdlib-only (json/urllib/re/html/time/sys), so it needs no venv
and is interpreter-portable (3.10+ floor).

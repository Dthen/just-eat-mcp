# just-eat-mcp

MCP server for Just Eat UK. No auth. Pure stdlib.

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
python3 server.py
```

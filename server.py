#!/usr/bin/env python3
"""Just Eat MCP Server v3.2 — search UK restaurants and menus via public API."""
import html as _html
import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "https://uk.api.just-eat.io"
DISCOVERY_URL = f"{BASE_URL}/discovery/uk/restaurants/enriched/bypostcode"
MENU_CDN_URL = "https://menu-globalmenucdn.je-apis.com"
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_RETRIES = 2
RETRY_DELAY = 2

# ── Era constants + stateless guards (migration card A.2, PLAN D2/D3; REFERENCE §1) ──
if hasattr(sys.stdin, "reconfigure"):            # binary/undecodable bytes must not kill the loop
    sys.stdin.reconfigure(errors="replace")      # invalid UTF-8 → U+FFFD → lands in the json.loads except

ERA_VERSION = "2026-07-28"
SERVER_INFO = {"name": "just-eat-mcp", "version": "3.2.0"}   # D7 minor bump (A.2 T05)
ERA_RESULT_FIELDS = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
RESULT_META = {"io.modelcontextprotocol/serverInfo": SERVER_INFO}


def era_result(payload):
    """A result carrying the era-strict fields D3 mandates on every response."""
    out = dict(payload)
    out.update(ERA_RESULT_FIELDS)
    out["_meta"] = RESULT_META
    return out


def safe_get(obj: dict, *keys, default=None):
    """Safely navigate nested dict without KeyError or None-crashing."""
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
    return obj if obj is not None else default


def clean(s) -> str:
    """Decode HTML entities and trim whitespace. Handles None, int, bool."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return _html.unescape(s).strip()


def api_get(url: str, timeout: int = 15) -> dict:
    """Fetch JSON from a URL with retry logic."""
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            last_err = str(e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))
        except Exception:
            raise
    return {"error": f"Request failed after {MAX_RETRIES + 1} attempts: {last_err}"}


def parse_limit(raw: any, default: int = 20) -> int:
    """Parse and validate a limit parameter (1-100)."""
    if raw is None or isinstance(raw, bool):
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    if val < 1:
        return default
    return min(val, 100)


def format_delivery_fee(restaurant_id: str, fee_data: dict) -> str:
    """Extract delivery fee bands — show cheapest paid tier or Free."""
    bands = safe_get(fee_data, str(restaurant_id), "bands", default=[])
    mov = safe_get(fee_data, str(restaurant_id), "minimumOrderValue", default=0)
    if not bands:
        return "Free"
    fees = [b.get("fee", 0) for b in bands if b.get("fee", 0) > 0]
    if not fees:
        return "Free"
    cheapest = min(fees) / 100
    if mov and mov > 1:
        return f"from £{cheapest:.2f} (min £{mov / 100:.0f})"
    return f"from £{cheapest:.2f}"


def format_restaurant(r: dict, fee_data: dict | None = None) -> str:
    """Format a restaurant into a markdown line. Handles both enriched (camelCase)
    and legacy (PascalCase) API response schemas."""
    # ── Name ──
    name = clean(r.get("name") or r.get("Name", "Unknown"))

    # ── Rating ──
    rating_obj = r.get("rating")
    if isinstance(rating_obj, dict):
        stars = rating_obj.get("starRating") or 0
        count = rating_obj.get("count") or 0
    else:
        stars = r.get("RatingStars") or 0
        count = r.get("NumberOfRatings") or 0
    rating_str = f"{stars}★"
    if count:
        rating_str += f" ({count})"

    # ── Cuisines ──
    raw_cuisines = r.get("cuisines") or r.get("CuisineTypes") or []
    if raw_cuisines:
        first = raw_cuisines[0]
        if isinstance(first, str):
            cuisines = ", ".join(clean(c) for c in raw_cuisines)
        else:
            cuisines = ", ".join(
                clean(c.get("name") or c.get("Name", "")) for c in raw_cuisines
            )
    else:
        cuisines = ""

    # ── URL ──
    url = r.get("url") or r.get("Url") or ""
    if not url:
        uname = r.get("uniqueName") or r.get("UniqueName") or ""
        if uname:
            url = f"https://www.just-eat.co.uk/restaurants-{uname}"

    # ── Address ──
    addr_obj = r.get("address") or r.get("Address") or {}
    if addr_obj:
        addr = clean(addr_obj.get("firstLine") or addr_obj.get("FirstLine", ""))
    else:
        addr = ""

    # ── Open status ──
    is_open = (
        safe_get(r, "availability", "delivery", "isOpen")
        or r.get("isOpenNow")
        or r.get("IsOpenNow")
    )
    open_icon = "🟢" if is_open else "🔴"

    # Collection availability
    has_collection = (
        r.get("isCollection")
        or r.get("IsCollection")
        or safe_get(r, "availability", "collection", "isOpen")
    )
    collection_str = " 📦" if has_collection else ""

    # ── Delivery fee ──
    rid = str(r.get("id") or r.get("Id", ""))
    fee_str = ""
    if fee_data and rid and rid in fee_data:
        fee_str = f" — Fee: {format_delivery_fee(rid, fee_data)}"
    else:
        dc = r.get("deliveryCost") or r.get("DeliveryCost")
        if dc is not None and dc > 0:
            fee_str = f" — Fee: £{dc:.2f}"

    # ── ETA (range) ──
    eta_low = (
        safe_get(r, "deliveryEtaMinutes", "rangeLower")
        or safe_get(r, "DeliveryEtaMinutes", "RangeLower")
    )
    eta_high = (
        safe_get(r, "deliveryEtaMinutes", "rangeUpper")
        or safe_get(r, "DeliveryEtaMinutes", "RangeUpper")
    )
    if eta_low and eta_high and eta_low != eta_high:
        eta_str = f" — {eta_low}–{eta_high}m"
    elif eta_high:
        eta_str = f" — ~{eta_high}m"
    else:
        eta_str = ""

    # ── Free delivery badge ──
    is_free = " 🚚" if (r.get("isFreeDelivery") or r.get("IsFreeDelivery")) else ""

    # ── Offers (legacy API) ──
    offers = r.get("Offers") or []
    offer_str = ""
    if offers:
        o = offers[0]
        desc = clean(o.get("Description", ""))
        if desc:
            offer_str = f" — 🔖 {desc}"

    # ── Deals (enriched API) ──
    deals = r.get("deals") or []
    deal_str = ""
    if deals:
        d = deals[0]
        desc = clean(d.get("description", ""))
        if desc:
            deal_str = f" — 🎁 {desc}"

    # ── Hygiene ──
    hygiene = r.get("hygieneRating") or r.get("HygieneRating")
    hygiene_str = f" — Hygiene: {hygiene}" if hygiene is not None else ""

    # ── Distance ──
    dist = r.get("driveDistanceMeters") or r.get("DriveDistance")
    dist_str = f" — {dist / 1000:.1f}km" if dist else ""

    return (
        f"- {open_icon}{collection_str}{is_free} **{name}** — {cuisines} — {rating_str}"
        f"{fee_str}{eta_str}{offer_str}{deal_str}"
        f"{hygiene_str}{dist_str} — {addr} — {url}"
    )


def _build_filter_desc(args: dict) -> tuple[str, bool]:
    """Build filter description string and whether any filters are active."""
    cuisine_filter = (args.get("cuisine") or "").lower()
    min_rating = args.get("min_rating") or 0
    open_only = args.get("open_only", False)
    active = bool(cuisine_filter or min_rating or open_only)
    parts = []
    if cuisine_filter:
        parts.append(f"'{args['cuisine']}'")
    if min_rating:
        parts.append(f"{min_rating}★+")
    if open_only:
        parts.append("open now")
    filter_str = f" ({', '.join(parts)})" if parts else ""
    return filter_str, active


TOOLS = [
    {
        "name": "search_restaurants",
        "description": "Search Just Eat restaurants by UK postcode. Returns names, cuisines, ratings, real delivery fees, ETAs, deals, distance, and URLs. Supports cuisine/rating/open filters and a limit parameter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "postcode": {"type": "string", "description": "UK postcode (e.g. 'KA7 1AA' or 'KA7')"},
                "cuisine": {"type": "string", "description": "Optional: filter by cuisine (e.g. 'Indian', 'Pizza')"},
                "min_rating": {"type": "number", "description": "Optional: minimum star rating (0-5)"},
                "open_only": {"type": "boolean", "description": "Optional: only show currently open for delivery"},
                "limit": {"type": "number", "description": "Optional: max results to return (default 20, max 100, min 1)"},
            },
            "required": ["postcode"],
        },
    },
    {
        "name": "search_by_location",
        "description": "Search Just Eat restaurants by latitude/longitude. Returns same data as search_restaurants. Useful for GPS/current-location searches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude (e.g. 55.462)"},
                "longitude": {"type": "number", "description": "Longitude (e.g. -4.634)"},
                "cuisine": {"type": "string", "description": "Optional: filter by cuisine"},
                "min_rating": {"type": "number", "description": "Optional: minimum star rating (0-5)"},
                "open_only": {"type": "boolean", "description": "Optional: only show currently open for delivery"},
                "limit": {"type": "number", "description": "Optional: max results (default 20, max 100)"},
            },
            "required": ["latitude", "longitude"],
        },
    },
    {
        "name": "get_curated_lists",
        "description": "Get server-curated restaurant lists for a postcode: Speedy Delivery ⚡, Top Rated, Deals 🔥, Save on Delivery 💸, and more. Returns restaurant IDs and names for each list. Pass a postcode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "postcode": {"type": "string", "description": "UK postcode (e.g. 'KA7 1AA')"},
            },
            "required": ["postcode"],
        },
    },
    {
        "name": "get_cuisines",
        "description": "Get available cuisine types and counts for a UK postcode on Just Eat.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "postcode": {"type": "string", "description": "UK postcode (e.g. 'KA7 1AA')"},
            },
            "required": ["postcode"],
        },
    },
    {
        "name": "get_menu",
        "description": "Get the full categorized menu for a Just Eat restaurant — all items with names, descriptions, prices, and customization options (modifier groups, deal groups). Pass the URL from search_restaurants results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "restaurant_url": {"type": "string", "description": "Full Just Eat restaurant URL from search results"},
            },
            "required": ["restaurant_url"],
        },
    },
    {
        "name": "get_restaurant_info",
        "description": "Get detailed restaurant information: full description, logo/banner images, opening hours, allergen URL, cuisines, address, and shareable link. Pass the URL from search_restaurants results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "restaurant_url": {"type": "string", "description": "Full Just Eat restaurant URL from search results"},
            },
            "required": ["restaurant_url"],
        },
    },
]


def handle_call(name: str, args: dict) -> str:
    # ── Validated argument access for required params ──
    def require(key: str):
        if key not in args:
            raise ValueError(f"missing required parameter '{key}'")
        return args[key]

    # ── search_restaurants ──
    if name == "search_restaurants":
        postcode = require("postcode").replace(" ", "").upper()
        if not postcode or not re.match(r"^[A-Z0-9]{2,8}$", postcode):
            return "Error: invalid UK postcode format"

        limit = parse_limit(args.get("limit"), default=20)
        cuisine_filter = (args.get("cuisine") or "").lower()
        min_rating = args.get("min_rating") or 0
        open_only = args.get("open_only", False)
        has_filters = bool(cuisine_filter or min_rating or open_only)
        fetch_limit = min(max(limit * 5, 100) if has_filters else limit, 200)

        data = api_get(f"{DISCOVERY_URL}/{postcode}?limit={fetch_limit}")
        if "error" in data:
            return f"Error: {data['error']}"

        restaurants = data.get("restaurants", [])
        delivery_fees = data.get("deliveryFees", {}).get("restaurants", {})

        if cuisine_filter:
            restaurants = [
                r for r in restaurants
                if any(
                    cuisine_filter in clean(
                        c if isinstance(c, str) else (c.get("name") or c.get("Name", ""))
                    ).lower()
                    for c in (r.get("cuisines") or r.get("cuisineTypes") or r.get("CuisineTypes") or [])
                )
            ]
        if min_rating:
            restaurants = [
                r for r in restaurants
                if (
                    (isinstance(r.get("rating"), dict) and (r["rating"].get("starRating") or 0) >= min_rating)
                    or ((r.get("RatingStars") or 0) >= min_rating)
                )
            ]
        if open_only:
            restaurants = [
                r for r in restaurants
                if r.get("isOpenNow") or safe_get(r, "availability", "delivery", "isOpen")
            ]

        filter_str, _ = _build_filter_desc(args)
        area = data.get("deliveryArea") or data.get("Area") or postcode
        total = data.get("metaData", {}).get("resultCount", len(restaurants))

        lines = [
            f"## {len(restaurants[:limit])} of {total} restaurants near {area}{filter_str}",
            f"Postcode: {postcode}",
            "",
        ]
        for r in restaurants[:limit]:
            lines.append(format_restaurant(r, delivery_fees))
        if len(restaurants) > limit:
            lines.append(f"\n... and {len(restaurants) - limit} more (increase limit to see all)")
        return "\n".join(lines)

    # ── search_by_location ──
    elif name == "search_by_location":
        lat = require("latitude")
        lon = require("longitude")
        url = f"{BASE_URL}/restaurants/bylatlong?latitude={lat}&longitude={lon}"
        data = api_get(url)
        if "error" in data:
            return f"Error: {data['error']}"

        restaurants = data.get("Restaurants", [])
        delivery_fees = data.get("deliveryFees", {}).get("restaurants", {})
        cuisine_filter = (args.get("cuisine") or "").lower()
        min_rating = args.get("min_rating") or 0
        open_only = args.get("open_only", False)
        limit = parse_limit(args.get("limit"), default=20)

        if cuisine_filter:
            restaurants = [
                r for r in restaurants
                if any(
                    cuisine_filter in clean(c.get("Name", "")).lower()
                    for c in (r.get("CuisineTypes") or [])
                )
            ]
        if min_rating:
            restaurants = [
                r for r in restaurants
                if (r.get("RatingStars") or 0) >= min_rating
            ]
        if open_only:
            restaurants = [r for r in restaurants if r.get("IsOpenNow")]

        filter_str, _ = _build_filter_desc(args)
        area = data.get("MetaData", {}).get("Area", f"{lat},{lon}")

        lines = [
            f"## {len(restaurants[:limit])} restaurants near {area} ({lat}, {lon}){filter_str}",
            "",
        ]
        for r in restaurants[:limit]:
            lines.append(format_restaurant(r, delivery_fees))
        if len(restaurants) > limit:
            lines.append(f"\n... and {len(restaurants) - limit} more (increase limit to see all)")
        return "\n".join(lines)

    # ── get_curated_lists ──
    elif name == "get_curated_lists":
        postcode = require("postcode").replace(" ", "").upper()
        if not postcode:
            return "Error: invalid postcode"

        data = api_get(f"{DISCOVERY_URL}/{postcode}?limit=200")
        if "error" in data:
            return f"Error: {data['error']}"

        # Index restaurants by ID for name lookup
        name_map = {
            str(r.get("id") or r.get("Id", "?")): r.get("name") or r.get("Name", "?")
            for r in data.get("restaurants", [])
        }

        # Parse layout.search-page for enrichedList blocks
        layout = data.get("layout", {}).get("search-page", {}).get("contents", [])
        lines = [f"## Curated Lists near {postcode}", ""]

        for block in layout:
            if block.get("type") != "enrichedList":
                continue
            title = clean(block.get("title", block.get("displayName", "?")))
            items = block.get("contents", [])
            restaurant_items = [
                it for it in items
                if it.get("type") == "restaurant" and it.get("id")
            ]
            if not restaurant_items:
                continue
            lines.append(f"### {title} ({len(restaurant_items)})")
            for it in restaurant_items[:10]:  # cap per list
                rid = str(it.get("id", "?"))
                rname = name_map.get(rid, f"ID:{rid}")
                lines.append(f"- {rname} (`{rid}`)")
            if len(restaurant_items) > 10:
                lines.append(f"  ... and {len(restaurant_items) - 10} more")
            lines.append("")

        if len(lines) == 2:
            return f"## Curated Lists near {postcode}\n\nNo curated lists available for this area."
        return "\n".join(lines)

    # ── get_cuisines ──
    elif name == "get_cuisines":
        postcode = require("postcode").replace(" ", "").upper()
        if not postcode:
            return "Error: invalid postcode"
        data = api_get(f"{BASE_URL}/restaurants/bypostcode/{postcode}")
        if "error" in data:
            return f"Error: {data['error']}"
        cuisines = safe_get(data, "MetaData", "CuisineDetails", default=[]) or []
        area = safe_get(data, "MetaData", "Area", default=postcode)
        lines = [f"## Cuisines near {area}"]
        for c in cuisines:
            lines.append(f"- {clean(c.get('Name', 'Unknown'))}: {c.get('Total', 0)} restaurants")
        return "\n".join(lines)

    # ── get_menu / get_restaurant_info ──
    elif name in ("get_menu", "get_restaurant_info"):
        with_categories = (name == "get_menu")
        return _get_menu(require("restaurant_url"), with_categories=with_categories)

    return f"Unknown tool: {name}"


def _get_menu(restaurant_url: str, with_categories: bool = True) -> str:
    """Shared implementation for get_menu and get_restaurant_info."""
    url = restaurant_url
    clean_url = url.split("?")[0].split("#")[0].rstrip("/")
    slug_match = re.search(r"restaurants-(.+?)(?:/menu)?$", clean_url)
    if not slug_match:
        return "Error: could not extract restaurant slug from URL"
    slug = slug_match.group(1)

    # Fetch manifest
    manifest = api_get(f"{MENU_CDN_URL}/{slug}_uk_manifest.json", timeout=20)
    if "error" in manifest:
        return f"Error fetching menu manifest: {manifest['error']}"

    ri = manifest.get("RestaurantInfo") or {}
    name = ri.get("Name") or slug.replace("-", " ").title()

    if not with_categories:
        return _format_restaurant_info(name, ri, manifest)

    # ── Full categorized menu ──
    # Fetch items
    items_url = manifest.get("ItemsUrl") or f"{slug}_uk_items.json"
    items_cdn = (
        items_url if items_url.startswith("http")
        else f"{MENU_CDN_URL}/{items_url}"
    )
    items_data = api_get(items_cdn, timeout=20)
    if "error" in items_data:
        return f"Error fetching menu items: {items_data['error']}"
    items_index = {i["Id"]: i for i in items_data.get("Items", []) if "Id" in i}

    # Fetch item details (modifier groups, deal groups)
    det_url = manifest.get("ItemDetailsUrl") or f"{slug}_uk_itemDetails.json"
    det_cdn = (
        det_url if det_url.startswith("http")
        else f"{MENU_CDN_URL}/{det_url}"
    )
    details = api_get(det_cdn, timeout=20)
    mod_groups = {}
    deal_groups = {}
    mod_sets = {}
    if "error" not in details:
        mod_groups = {mg["Id"]: mg for mg in details.get("ModifierGroups", []) if "Id" in mg}
        deal_groups = {dg["Id"]: dg for dg in details.get("DealGroups", []) if "Id" in dg}
        mod_sets = {ms["Id"]: ms for ms in details.get("ModifierSets", []) if "Id" in ms}

    total_items = len(items_index)
    lines = [f"## {name} — Menu", f"({total_items} items across categories)", ""]
    # Build categorized output from manifest menus, deduplicating categories
    menus = manifest.get("Menus") or []
    seen_cats = set()
    for menu in menus:
        for cat in (menu.get("Categories") or []):
            cat_name = clean(cat.get("Name", "Unknown"))
            item_ids = cat.get("ItemIds") or []
            if not item_ids or cat_name in seen_cats:
                continue
            seen_cats.add(cat_name)
            cat_items = [items_index[iid] for iid in item_ids if iid in items_index]
            if not cat_items:
                continue
            lines.append(f"### {cat_name} ({len(cat_items)} items)")
            for item in cat_items:
                _append_item_line(lines, item, mod_groups, deal_groups, mod_sets)
            lines.append("")

    # Orphaned items
    categorized_ids = set()
    for menu in (menus or []):
        for cat in (menu.get("Categories") or []):
            categorized_ids.update(cat.get("ItemIds") or [])
    orphaned = len(items_index) - len(categorized_ids & set(items_index))
    if orphaned > 0:
        lines.append(f"({orphaned} items not in any category)")
        lines.append("")

    return "\n".join(lines)


def _format_restaurant_info(name: str, ri: dict, manifest: dict) -> str:
    """Format get_restaurant_info output."""
    lines = [f"## {name} — Restaurant Info", ""]

    desc = clean(ri.get("Description", ""))
    if desc:
        lines.append(f"{desc}\n")

    logo = ri.get("LogoUrl")
    banner = ri.get("BannerUrl")
    if logo:
        lines.append(f"Logo: {logo}")
    if banner:
        lines.append(f"Banner: {banner}")

    cuisines = [clean(c.get("Name", "")) for c in (ri.get("CuisineTypes") or [])]
    if cuisines:
        lines.append(f"\nCuisines: {', '.join(cuisines)}")

    location = ri.get("Location")
    if location:
        addr_parts = [
            clean(location.get(k, ""))
            for k in ("AddressLine1", "AddressLine2", "City", "Postcode")
        ]
        addr = ", ".join(p for p in addr_parts if p)
        if addr:
            lines.append(f"Address: {addr}")

    allergen = ri.get("AllergenUrl")
    if allergen:
        lines.append(f"Allergen info: {allergen}")

    share = ri.get("ShareableLink")
    if share:
        lines.append(f"Shareable link: {share}")

    hours = ri.get("RestaurantOpeningTimes") or []
    if hours:
        lines.append("\n### Opening Hours")
        for h in hours:
            stype = clean(h.get("ServiceType", "delivery"))
            lines.append(f"**{stype}:**")
            for day_data in (h.get("TimesPerDay") or []):
                day = clean(day_data.get("DayOfWeek", ""))
                times_list = day_data.get("Times") or []
                time_strs = [
                    f"{t.get('FromLocalTime', '')}–{t.get('ToLocalTime', '')}"
                    for t in times_list
                ]
                if time_strs:
                    lines.append(f"  {day}: {', '.join(time_strs)}")
            lines.append("")

    tags = ri.get("Tags") or []
    if tags:
        lines.append(f"Tags: {', '.join(clean(t) for t in tags)}")

    lines.append(f"\nuuid: {manifest.get('RestaurantId', '')}")
    return "\n".join(lines)


def _append_item_line(
    lines: list, item: dict,
    mod_groups: dict | None = None,
    deal_groups: dict | None = None,
    mod_sets: dict | None = None,
) -> None:
    """Format a single menu item line with customization and nutrition data."""
    mod_groups = mod_groups or {}
    deal_groups = deal_groups or {}
    mod_sets = mod_sets or {}

    item_name = clean(item.get("Name", "?"))
    desc = clean(item.get("Description", ""))
    img = (item.get("ImageSources") or [None])[0]
    if isinstance(img, dict):
        img = img.get("Path", "")
    servings = safe_get(item, "NumberOfServings", "ServingsDisplay", default="")

    # Energy
    energy = safe_get(item, "EnergyContent", "EnergyDisplay", default="")
    if energy:
        energy = clean(energy)

    # Price-per-unit
    ppu = safe_get(item, "InitialProductInformation", "PricePerUnit", default="")
    if ppu:
        ppu = clean(ppu)

    # Variations / prices
    variations = item.get("Variations") or []
    prices = []
    for v in variations:
        bp = v.get("BasePrice")
        vname = clean(v.get("Name", ""))
        if bp is not None:
            label = f"{vname} £{bp:.2f}" if vname else f"£{bp:.2f}"
            prices.append(label)
    price_str = " | ".join(prices[:4]) if prices else "—"

    # Labels
    labels = [clean(lbl) for lbl in (item.get("Labels") or [])]
    label_str = f" [{', '.join(labels)}]" if labels else ""

    # Modifier groups (customization options)
    mod_strs = []
    seen_mg = set()
    for v in variations:
        for mg_id in (v.get("ModifierGroupsIds") or []):
            mg = mod_groups.get(mg_id)
            if not mg or mg_id in seen_mg:
                continue
            seen_mg.add(mg_id)
            mod_names = [
                clean(mod_sets.get(mid, {}).get("Modifier", {}).get("Name", "?"))
                for mid in (mg.get("Modifiers") or [])
            ][:4]
            mg_name = clean(mg.get("Name", ""))
            mg_info = f"{mg_name}: {', '.join(mod_names)}"
            if len(mg.get("Modifiers") or []) > 4:
                mg_info += f" +{len(mg['Modifiers']) - 4} more"
            mod_strs.append(mg_info)

    # Deal groups
    deal_strs = []
    seen_dg = set()
    for v in variations:
        for dg_id in (v.get("DealGroupsIds") or []):
            dg = deal_groups.get(dg_id)
            if not dg or dg_id in seen_dg:
                continue
            seen_dg.add(dg_id)
            dg_name = clean(dg.get("Name", "?"))
            dg_items = [
                clean(di.get("Name", "?"))
                for di in (dg.get("Items") or [])
            ][:3]
            dg_info = f"Deal: {dg_name}"
            if dg_items:
                dg_info += f" ({', '.join(dg_items)})"
            deal_strs.append(dg_info)

    # Extras
    extras = []
    if servings:
        extras.append(servings)
    if energy:
        extras.append(energy)
    if ppu:
        extras.append(ppu)
    if img:
        extras.append(f"[image]({img})")
    for m_str in mod_strs:
        extras.append(m_str)
    for d_str in deal_strs:
        extras.append(d_str)

    parts = [f"- **{item_name}** ({price_str})"]
    if desc:
        parts.append(f" — {desc}")
    if label_str:
        parts.append(label_str)
    if extras:
        parts.append(f" ∙ {' ∙ '.join(extras)}")

    lines.append("".join(parts))


def send(resp: dict) -> None:
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:                      # EOF on stdin ends the loop (§7)
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue                            # garbage lines: skip, NEVER die (§7)
        if not isinstance(req, dict):           # valid JSON, not an object ("5", null, [1,2]): skip, NEVER die (§7)
            continue
        rid = req.get("id")                     # str or int; absent ⇒ notification
        method = req.get("method")              # null/42/etc must not crash .startswith below
        if not isinstance(method, str):
            method = ""                         # route as unknown-method

        if method == "server/discover":         # §2 (the one era entry point; initialize is gone — §3)
            send({"jsonrpc": "2.0", "id": rid, "result": era_result({
                "supportedVersions": [ERA_VERSION],
                "capabilities": {"tools": {}}})})
        elif method == "tools/list":            # §4 (TOOLS byte-frozen per D4 golden)
            send({"jsonrpc": "2.0", "id": rid, "result": era_result({"tools": TOOLS})})
        elif method == "tools/call":            # §5
            params = req.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602,
                    "message": "missing required param: params (with string 'name')"}})
                continue
            try:
                result = handle_call(params["name"], params.get("arguments", {}))
            except Exception as e:              # dispatch-level only (shouldn't happen)
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(e)}})
                continue
            # DEVIATION from REFERENCE §5 (justified: R3 owner ruling — string-result wire drift
            # kept verbatim per §5's pass-through convention ("String results: pass through
            # verbatim"); legacy error text "Error: …" rides in content as-is, no isError, since
            # handle_call never returns dicts here. Note: §5's "do not DEVIATION-tag" line covers
            # loop structure only — R3 mandates the tag for this wire drift. Pin tests: T03 #7/#8, T02.)
            send({"jsonrpc": "2.0", "id": rid,
                  "result": era_result({"content": [{"type": "text", "text": result}]})})
        elif method.startswith("notifications/"):  # §6: consume silently, never respond
            pass
        elif method == "ping":                  # §6: the {} form (lenient EmptyResult tolerates it)
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:                                   # §3 catch-all
            # includes legacy `initialize` (D2) and every other unknown method, per JSON-RPC
            if rid is None and "id" not in req:
                continue                        # no-id = notification: never respond
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}})


if __name__ == "__main__":
    main()

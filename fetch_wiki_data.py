"""Fetch item and harvest data from ark.wiki.gg API and generate Python data modules."""
import json
import re
import urllib.request
import urllib.parse
import sys

API_URL = "https://ark.wiki.gg/api.php"

ITEM_SUBPAGES = [
    "Resources", "Tools", "Armor", "Saddles", "Structures", "Vehicles",
    "Dye", "Consumables", "Recipes", "Eggs", "Farming", "Seeds",
    "Weapons", "Ammunition", "Skins", "Artifacts", "Trophy",
]


def fetch_wikitext(page_title: str) -> str:
    """Fetch wikitext for a page via the MediaWiki API."""
    params = urllib.parse.urlencode({
        "action": "parse",
        "page": page_title,
        "prop": "wikitext",
        "format": "json",
    })
    url = f"{API_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "ARK-ASA-Manager/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("parse", {}).get("wikitext", {}).get("*", "")


def parse_items(wikitext: str):
    """Parse {{Id item|...}} templates from wikitext.
    
    Returns list of (name, category, stack_size, class_name) tuples.
    """
    items = []
    # Match {{Id item|Name|Category|StackSize|ItemID|ClassName|BlueprintPath|...}}
    pattern = re.compile(r'\{\{Id item\|([^}]+)\}\}', re.IGNORECASE)
    for m in pattern.finditer(wikitext):
        parts = m.group(1).split("|")
        if len(parts) < 5:
            continue
        name = parts[0].strip()
        category = parts[1].strip()
        stack_size = parts[2].strip()
        # parts[3] is item ID
        class_name = parts[4].strip()
        if not class_name or class_name == "-":
            continue
        # Try to parse stack size as int
        try:
            stack_int = int(stack_size)
        except (ValueError, TypeError):
            stack_int = 0
        items.append((name, category, stack_int, class_name))
    return items


def main():
    all_items = []
    for subpage in ITEM_SUBPAGES:
        page_title = f"Item_IDs/{subpage}"
        print(f"Fetching {page_title}...", file=sys.stderr)
        try:
            wikitext = fetch_wikitext(page_title)
            items = parse_items(wikitext)
            print(f"  Found {len(items)} items", file=sys.stderr)
            all_items.extend(items)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)

    # Deduplicate by class name (keep first occurrence)
    seen = set()
    unique_items = []
    for name, category, stack_size, class_name in all_items:
        if class_name not in seen:
            seen.add(class_name)
            unique_items.append((name, category, stack_size, class_name))

    unique_items.sort(key=lambda x: x[0])

    print(f"\nTotal unique items: {len(unique_items)}", file=sys.stderr)

    # Generate ark_item_data.py
    lines = [
        '# ARK item data - auto-generated from ark.wiki.gg/wiki/Item_IDs',
        '#',
        '# name -> (class_name, category, default_stack_size)',
        '# =============================================================================',
        '',
        '_ITEM_DATA: dict[str, tuple[str, str, int]] = {',
    ]
    for name, category, stack_size, class_name in unique_items:
        # Escape quotes in name
        safe_name = name.replace("'", "\\'")
        safe_category = category.replace("'", "\\'")
        lines.append(f"    '{safe_name}': ('{class_name}', '{safe_category}', {stack_size}),")
    lines.append('}')
    lines.append('')
    lines.append('_ITEM_NAMES_SORTED: list[str] = sorted(_ITEM_DATA.keys())')
    lines.append('')
    lines.append('_CLASS_TO_ITEM_NAME: dict[str, str] = {v[0]: k for k, v in _ITEM_DATA.items()}')
    lines.append('')

    with open("data/ark_item_data.py", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Written data/ark_item_data.py", file=sys.stderr)

    # Generate ark_harvest_data.py - resources subset
    resource_items = [(n, c, s, cn) for n, c, s, cn in unique_items if c == "Resources"]
    resource_items.sort(key=lambda x: x[0])

    hlines = [
        '# ARK harvestable resource data - auto-generated from ark.wiki.gg/wiki/Item_IDs/Resources',
        '#',
        '# name -> class_name',
        '# =============================================================================',
        '',
        '_HARVEST_RESOURCES: dict[str, str] = {',
    ]
    for name, category, stack_size, class_name in resource_items:
        safe_name = name.replace("'", "\\'")
        hlines.append(f"    '{safe_name}': '{class_name}',")
    hlines.append('}')
    hlines.append('')
    hlines.append('_HARVEST_NAMES_SORTED: list[str] = sorted(_HARVEST_RESOURCES.keys())')
    hlines.append('')
    hlines.append('_HARVEST_CLASS_TO_NAME: dict[str, str] = {v: k for k, v in _HARVEST_RESOURCES.items()}')
    hlines.append('')

    with open("data/ark_harvest_data.py", "w", encoding="utf-8") as f:
        f.write("\n".join(hlines))
    print("Written data/ark_harvest_data.py", file=sys.stderr)


if __name__ == "__main__":
    main()

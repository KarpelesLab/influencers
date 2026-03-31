#!/usr/bin/env python3
"""Reserved username database for protecting notable figures on a new SNS.

All data lives in YAML files under data/ — no database needed.
"""

import csv
import io
import re
import sys
import time
import unicodedata
from pathlib import Path

import click
import requests
import yaml
from rich.console import Console
from rich.table import Table

DATA_DIR = Path(__file__).parent / "data"

PLATFORMS = [
    "twitter",
    "instagram",
    "youtube",
    "tiktok",
    "facebook",
    "threads",
    "bluesky",
    "mastodon",
]

PLATFORM_URL_TEMPLATES = {
    "twitter": "https://x.com/{handle}",
    "instagram": "https://instagram.com/{handle}",
    "youtube": "https://youtube.com/@{handle}",
    "tiktok": "https://tiktok.com/@{handle}",
    "facebook": "https://facebook.com/{handle}",
    "threads": "https://threads.net/@{handle}",
    "bluesky": "https://bsky.app/profile/{handle}",
    "mastodon": None,  # user@server format, needs special handling
}

VALID_CATEGORIES = [
    "celebrity",
    "influencer",
    "politician",
    "journalist",
    "brand",
    "organization",
]

def _pluralize(cat: str) -> str:
    if cat.endswith("y"):
        return cat[:-1] + "ies"
    return cat + "s"

CATEGORY_TO_FILENAME = {cat: _pluralize(cat) + ".yaml" for cat in VALID_CATEGORIES}

FILENAME_TO_CATEGORY = {
    "celebrities": "celebrity",
    "influencers": "influencer",
    "politicians": "politician",
    "journalists": "journalist",
    "brands": "brand",
    "organizations": "organization",
}

console = Console()


# --- Normalization ---

def normalize_username(raw: str) -> str:
    """Minimal normalization: strip @, lowercase, strip whitespace.

    Actual username rules are left to the platform. This just provides
    a consistent key for lookups in our data.
    """
    return raw.strip().lstrip("@").lower()


def suggest_variants(username: str) -> list[str]:
    """Suggest additional username variants a platform may want to also reserve.

    These are advisory — the platform decides which to actually block.
    Returns a list of variant strings (not including the original).
    """
    base = normalize_username(username)
    variants = set()

    # Without dots, underscores, hyphens
    stripped = re.sub(r"[._\-]", "", base)
    if stripped != base:
        variants.add(stripped)

    # With dots/underscores/hyphens swapped
    for old, new in [(".", "_"), ("_", "."), ("-", "_"), ("_", "-"), (".", "-"), ("-", ".")]:
        v = base.replace(old, new)
        if v != base:
            variants.add(v)

    # Without accents (for names like cafe -> cafe)
    nfkd = unicodedata.normalize("NFKD", base)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    if ascii_only != base:
        variants.add(ascii_only)

    variants.discard(base)
    return sorted(variants)


# --- YAML data layer ---

def get_platform_url(platform: str, handle: str) -> str:
    """Build a profile URL for a platform handle."""
    if platform == "mastodon":
        if "@" in handle:
            user, server = handle.split("@", 1)
            return f"https://{server}/@{user}"
        return handle
    tmpl = PLATFORM_URL_TEMPLATES.get(platform)
    if tmpl:
        return tmpl.format(handle=handle)
    return handle


def load_all_entries(data_dir: Path = DATA_DIR) -> list[dict]:
    """Load all entries from all YAML files, tagged with their category."""
    entries = []
    if not data_dir.exists():
        return entries
    for f in sorted(data_dir.glob("*.yaml")) + sorted(data_dir.glob("*.yml")):
        category = FILENAME_TO_CATEGORY.get(f.stem)
        if not category:
            continue
        data = yaml.safe_load(f.read_text()) or []
        for entry in data:
            entry["_category"] = category
            entry["_file"] = str(f)
            entries.append(entry)
    return entries


def load_category(category: str, data_dir: Path = DATA_DIR) -> tuple[list[dict], Path]:
    """Load entries for a single category. Returns (entries, filepath)."""
    filename = CATEGORY_TO_FILENAME[category]
    filepath = data_dir / filename
    if filepath.exists():
        entries = yaml.safe_load(filepath.read_text()) or []
    else:
        entries = []
    return entries, filepath


def save_category(entries: list[dict], filepath: Path):
    """Save entries to a YAML file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    # Clean entries: remove internal fields before saving
    clean = []
    for e in entries:
        c = {k: v for k, v in e.items() if not k.startswith("_")}
        clean.append(c)
    filepath.write_text(yaml.dump(clean, default_flow_style=False, allow_unicode=True, sort_keys=False))


COMPANY_SUFFIXES_RE = re.compile(
    r",?\s*\b(inc\.?|corp\.?|co\.?|llc|lp|ltd\.?|plc|ag|sa|nv|se|ab|asa|"
    r"incorporated|corporation|company|limited|group|holdings?|enterprises?|"
    r"international|technologies|technology|systems|solutions|partners|"
    r"/adr|/de/|/new)\b.*$",
    re.IGNORECASE,
)


def strip_company_suffix(name: str) -> str:
    """Strip common corporate suffixes for matching. 'Tron Inc.' -> 'tron'."""
    s = COMPANY_SUFFIXES_RE.sub("", name)
    return s.strip().strip(".,&/ ").strip()


def find_entry_by_name(entries: list[dict], query: str) -> dict | None:
    """Find an entry by name (case-insensitive), stripping corporate suffixes."""
    needle = normalize_username(query)
    for entry in entries:
        name = entry.get("name", "")
        if normalize_username(name) == needle:
            return entry
        if normalize_username(strip_company_suffix(name)) == needle:
            return entry
    return None


def find_entry_by_handle(entries: list[dict], username: str) -> dict | None:
    """Find an entry that uses this handle on any platform."""
    needle = normalize_username(username)
    for entry in entries:
        for plat in PLATFORMS:
            val = entry.get("handles", {}).get(plat)
            if not val:
                continue
            handle_list = val if isinstance(val, list) else [val]
            for h in handle_list:
                if normalize_username(h) == needle:
                    return entry
    return None


def entry_handles_list(entry: dict) -> list[dict]:
    """Expand handles dict into a list of {platform, handle, url}."""
    result = []
    for plat in PLATFORMS:
        val = entry.get("handles", {}).get(plat)
        if not val:
            continue
        handle_list = val if isinstance(val, list) else [val]
        for h in handle_list:
            result.append({
                "platform": plat,
                "handle": h,
                "url": get_platform_url(plat, h),
            })
    return result


def print_entry(entry: dict, category: str = None):
    """Pretty-print an entry."""
    cat = category or entry.get("_category", "?")
    name = entry.get("name", "?")
    table = Table(title=name, show_header=False, title_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Category", cat)
    if entry.get("description"):
        table.add_row("Description", entry["description"])
    if entry.get("wikidata_id"):
        table.add_row("Wikidata", entry["wikidata_id"])
    if entry.get("stock_exchange"):
        table.add_row("Stock", entry["stock_exchange"])
    if entry.get("death"):
        table.add_row("Death", str(entry["death"]))
    for h in entry_handles_list(entry):
        table.add_row(h["platform"], f"@{h['handle']}\n{h['url']}")
    console.print(table)


# --- CLI ---

@click.group()
def cli():
    """Reserved username database for protecting notable figures."""
    pass


@cli.command()
@click.argument("username")
def check(username):
    """Check if a username is reserved (matches against actual handles)."""
    entries = load_all_entries()
    slug = normalize_username(username)
    # Check against actual handles used on other platforms
    entry = find_entry_by_handle(entries, slug)
    if not entry:
        # Also check against entry names
        entry = find_entry_by_name(entries, slug)
    if entry:
        cat = entry.get("_category", "?")
        console.print(f"[bold red]RESERVED[/bold red]: \"{entry['name']}\" ({cat})")
        if entry.get("death"):
            console.print(f"  [dim]Deceased: {entry['death']}[/dim]")
        for h in entry_handles_list(entry):
            console.print(f"  {h['platform']}: @{h['handle']}")
        sys.exit(1)
    else:
        console.print(f"[bold green]AVAILABLE[/bold green]: \"{username}\" is not reserved.")
        variants = suggest_variants(slug)
        if variants:
            console.print(f"\n[dim]Suggested variants your platform may also want to reserve:[/dim]")
            for v in variants:
                v_entry = find_entry_by_handle(entries, v)
                if v_entry:
                    console.print(f"  {v} [red](reserved by {v_entry['name']})[/red]")
                else:
                    console.print(f"  {v}")
        sys.exit(0)


@cli.command()
@click.argument("query")
def lookup(query):
    """Look up a person by name or handle."""
    entries = load_all_entries()

    # Exact match on name or handle
    entry = find_entry_by_name(entries, query) or find_entry_by_handle(entries, query)
    if entry:
        print_entry(entry)
        return

    # Partial name match
    query_lower = query.lower()
    matches = [e for e in entries if query_lower in e.get("name", "").lower()
               or query_lower in e.get("description", "").lower()]
    if matches:
        for e in matches[:10]:
            print_entry(e)
    else:
        console.print(f"[yellow]No results for \"{query}\".[/yellow]")


@cli.command()
@click.option("--name", required=True, help="Display name.")
@click.option("--category", required=True, type=click.Choice(VALID_CATEGORIES))
@click.option("--description", default=None, help="Short description.")
@click.option("--twitter", default=None)
@click.option("--instagram", default=None)
@click.option("--youtube", default=None)
@click.option("--tiktok", default=None)
@click.option("--facebook", default=None)
@click.option("--threads", default=None)
@click.option("--bluesky", default=None)
@click.option("--mastodon", default=None)
@click.option("--death", default=None, help="Date of death (e.g. 2025-01-15 or 2025).")
def add(name, category, description, death, **platform_handles):
    """Add a person to the appropriate YAML file."""
    entries, filepath = load_category(category)

    # Check for duplicates
    existing = find_entry_by_name(entries, name)
    if existing:
        console.print(f"[yellow]Already exists[/yellow]: {existing['name']} in {filepath.name}")
        return

    entry = {"name": name}
    if description:
        entry["description"] = description
    if death:
        entry["death"] = death
    handles = {k: v for k, v in platform_handles.items() if v}
    if handles:
        entry["handles"] = handles

    entries.append(entry)
    entries.sort(key=lambda e: e.get("name", "").lower())
    save_category(entries, filepath)
    console.print(f"[green]Added[/green]: {name} to {filepath.name}")


@cli.command()
@click.argument("name_or_alias")
@click.option("--confirm", is_flag=True, help="Confirm deletion.")
def remove(name_or_alias, confirm):
    """Remove a person from their YAML file."""
    for category in VALID_CATEGORIES:
        entries, filepath = load_category(category)
        entry = find_entry_by_name(entries, name_or_alias)
        if entry:
            if not confirm:
                console.print(f"Would remove: {entry['name']} from {filepath.name}. Use --confirm to proceed.")
                return
            entries.remove(entry)
            save_category(entries, filepath)
            console.print(f"[red]Removed[/red]: {entry['name']} from {filepath.name}")
            return
    console.print(f"[yellow]Not found: {name_or_alias}[/yellow]")


@cli.command()
@click.argument("query")
@click.option("--category", default=None, type=click.Choice(VALID_CATEGORIES))
@click.option("--platform", default=None, type=click.Choice(PLATFORMS))
def search(query, category, platform):
    """Search across names, descriptions, and handles."""
    entries = load_all_entries()
    query_lower = query.lower()
    results = []

    for entry in entries:
        if category and entry.get("_category") != category:
            continue

        matched = False
        # Name / description match
        if query_lower in entry.get("name", "").lower():
            matched = True
        elif query_lower in entry.get("description", "").lower():
            matched = True

        # Handle match
        if not matched:
            for h in entry_handles_list(entry):
                if platform and h["platform"] != platform:
                    continue
                if query_lower in h["handle"].lower():
                    matched = True
                    break

        if matched:
            results.append(entry)

    if not results:
        console.print(f"[yellow]No results for \"{query}\".[/yellow]")
        return

    console.print(f"[cyan]Found {len(results)} result(s):[/cyan]\n")
    for entry in results:
        print_entry(entry)


@cli.command()
@click.argument("name_or_username")
def verify(name_or_username):
    """Show verification requirements for claiming a reserved name."""
    entries = load_all_entries()

    # Try exact match first
    entry = find_entry_by_name(entries, name_or_username) or find_entry_by_handle(entries, name_or_username)
    if not entry:
        # Try partial name match
        query_lower = name_or_username.lower()
        for e in entries:
            if query_lower in e.get("name", "").lower():
                entry = e
                break

    if not entry:
        console.print(f"[yellow]\"{name_or_username}\" is not in the reserved names database.[/yellow]")
        return

    handles = entry_handles_list(entry)
    if not handles:
        console.print(f"[yellow]No verification handles on file for {entry['name']}.[/yellow]")
        return

    cat = entry.get("_category", "?")
    console.print(f"\n[bold]Verification for: {entry['name']}[/bold] ({cat})")
    if entry.get("description"):
        console.print(f"  {entry['description']}\n")

    console.print(
        "To claim this username, prove ownership of [bold]at least one[/bold] "
        "of these accounts:\n"
    )

    table = Table()
    table.add_column("#", style="dim")
    table.add_column("Platform", style="bold")
    table.add_column("Handle")
    table.add_column("URL")

    for i, h in enumerate(handles, 1):
        table.add_row(str(i), h["platform"], f"@{h['handle']}", h["url"])

    console.print(table)
    console.print(
        "\n[dim]Suggested method: Ask the claimant to post a verification code "
        "on one of the accounts listed above, then confirm the post exists.[/dim]"
    )


@cli.command()
def stats():
    """Show statistics about the reserved names data."""
    entries = load_all_entries()
    if not entries:
        console.print("[yellow]No data. Add entries to data/ YAML files first.[/yellow]")
        return

    console.print(f"\n[bold]Database Statistics[/bold]\n")
    console.print(f"Total entries: [bold]{len(entries)}[/bold]\n")

    # By category
    cat_counts = {}
    for e in entries:
        cat = e.get("_category", "?")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    table = Table(title="By Category")
    table.add_column("Category")
    table.add_column("Count", justify="right")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        table.add_row(cat, str(count))
    console.print(table)

    # Handle coverage
    table = Table(title="Handle Coverage")
    table.add_column("Platform")
    table.add_column("Entries with handle", justify="right")
    table.add_column("Coverage", justify="right")
    total = len(entries)
    for plat in PLATFORMS:
        count = sum(1 for e in entries if plat in e.get("handles", {}))
        pct = f"{count / total * 100:.1f}%" if total else "0%"
        table.add_row(plat, str(count), pct)
    console.print(table)

    # Alias count
    handle_count = sum(len(entry_handles_list(e)) for e in entries)
    console.print(f"\nTotal handles tracked: {handle_count}")


@cli.command()
@click.option("--format", "fmt", type=click.Choice(["csv"]), default="csv")
@click.option("--category", default=None, type=click.Choice(VALID_CATEGORIES))
@click.option("--output", "-o", default=None, help="Output file (default: stdout).")
def export(fmt, category, output):
    """Export all data to CSV."""
    entries = load_all_entries()
    if category:
        entries = [e for e in entries if e.get("_category") == category]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "category", "description"] + PLATFORMS)
    for e in sorted(entries, key=lambda x: (x.get("_category", ""), x.get("name", ""))):
        handles = e.get("handles", {})
        writer.writerow(
            [e.get("name", ""), e.get("_category", ""), e.get("description", "")]
            + [handles.get(plat, "") if not isinstance(handles.get(plat), list)
               else ";".join(handles.get(plat, []))
               for plat in PLATFORMS]
        )
    text = buf.getvalue()

    if output:
        Path(output).write_text(text)
        console.print(f"[green]Exported to {output}[/green]")
    else:
        console.print(text)


@cli.command()
def platforms():
    """List supported platforms."""
    for p in PLATFORMS:
        tmpl = PLATFORM_URL_TEMPLATES.get(p, "")
        console.print(f"  {p}: {tmpl or '(special format)'}")


# --- Wikidata seeding ---

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

WIKIDATA_OCCUPATION_MAP = {
    "celebrity": [
        "Q33999",     # actor
        "Q177220",    # singer
        "Q639669",    # musician
        "Q10800557",  # film star
        "Q2405480",   # voice actor
        "Q3455803",   # director
        "Q36180",     # writer
        "Q49757",     # poet
        "Q1028181",   # painter
        "Q2066131",   # athlete
        "Q937857",    # football player
        "Q3665646",   # basketball player
        "Q10843263",  # tennis player
        "Q11338576",  # boxer
    ],
    "influencer": [
        "Q15627169",  # social media personality / content creator
    ],
    "politician": [
        "Q82955",     # politician
        "Q193391",    # diplomat
        "Q11774202",  # head of state
        "Q30461",     # president
    ],
    "journalist": [
        "Q1930187",   # journalist
        "Q947873",    # television presenter
        "Q3286043",   # radio host
    ],
}

WIKIDATA_PLATFORM_PROPS = {
    "twitter": "P2002",
    "instagram": "P2003",
    "youtube": "P11245",
    "tiktok": "P7085",
    "facebook": "P2013",
    "threads": "P11892",
    "bluesky": "P12361",
    "mastodon": "P4033",
}


def build_sparql_query(occupation_qid, limit=2000):
    """Build a SPARQL query for one occupation type."""
    optional_extras = []
    select_extras = []
    for platform, prop in WIKIDATA_PLATFORM_PROPS.items():
        if platform in ("twitter", "instagram"):
            continue
        var = f"?{platform}"
        select_extras.append(var)
        optional_extras.append(f"  OPTIONAL {{ ?person wdt:{prop} {var} . }}")

    extras_select = " ".join(select_extras)
    extras_optional = "\n".join(optional_extras)

    return f"""
SELECT ?person ?personLabel ?personDescription ?twitter ?instagram {extras_select}
WHERE {{
  ?person wdt:P31 wd:Q5 .
  ?person wdt:P106 wd:{occupation_qid} .

  OPTIONAL {{ ?person wdt:P2002 ?twitter . }}
  OPTIONAL {{ ?person wdt:P2003 ?instagram . }}

{extras_optional}

  FILTER(BOUND(?twitter) || BOUND(?instagram))

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
}}
LIMIT {limit}
"""


def run_sparql_query(query):
    """Execute a SPARQL query against Wikidata."""
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": "ReservedNamesBot/1.0 (SNS username protection tool)",
    }
    resp = requests.get(WIKIDATA_SPARQL_URL, params={"query": query}, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


@cli.command()
@click.option("--category", default=None, type=click.Choice(list(WIKIDATA_OCCUPATION_MAP.keys())),
              help="Seed only this category.")
@click.option("--limit", default=2000, help="Max results per occupation query.")
@click.option("--dry-run", is_flag=True, help="Show what would be imported without writing.")
def seed(category, limit, dry_run):
    """Seed from Wikidata SPARQL queries. Writes results into YAML files."""
    categories = [category] if category else list(WIKIDATA_OCCUPATION_MAP.keys())
    total_added = total_skipped = 0
    seen_qids = set()

    for cat in categories:
        qids = WIKIDATA_OCCUPATION_MAP.get(cat, [])
        if not qids:
            continue

        # Load existing entries for merge
        existing_entries, filepath = load_category(cat)
        existing_names = {normalize_username(e.get("name", "")) for e in existing_entries}
        existing_wikidata = {e.get("wikidata_id") for e in existing_entries if e.get("wikidata_id")}
        cat_added = cat_skipped = 0

        for qid_code in qids:
            console.print(f"[cyan]Querying Wikidata for {cat} ({qid_code})...[/cyan]")
            query = build_sparql_query(qid_code, limit=limit)

            try:
                results = run_sparql_query(query)
            except Exception as e:
                console.print(f"[red]  Query failed: {e}[/red]")
                time.sleep(5)
                continue

            console.print(f"  Got {len(results)} results.")

            for row in results:
                entity_uri = row.get("person", {}).get("value", "")
                qid = entity_uri.rsplit("/", 1)[-1] if entity_uri else None
                if not qid or qid in seen_qids:
                    continue
                seen_qids.add(qid)

                name = row.get("personLabel", {}).get("value", "")
                if not name or name == qid:
                    cat_skipped += 1
                    continue

                # Skip if already exists
                if qid in existing_wikidata or normalize_username(name) in existing_names:
                    cat_skipped += 1
                    continue

                description = row.get("personDescription", {}).get("value")
                handles = {}
                for platform in WIKIDATA_PLATFORM_PROPS:
                    val = row.get(platform, {}).get("value")
                    if val:
                        handles[platform] = val

                if not handles:
                    cat_skipped += 1
                    continue

                if dry_run:
                    handle_str = ", ".join(f"{p}=@{h}" for p, h in handles.items())
                    console.print(f"  [dim]{name} ({qid}): {handle_str}[/dim]")
                    cat_added += 1
                    continue

                entry = {"name": name}
                if description:
                    entry["description"] = description
                entry["wikidata_id"] = qid
                if handles:
                    entry["handles"] = handles
                existing_entries.append(entry)
                existing_names.add(normalize_username(name))
                existing_wikidata.add(qid)
                cat_added += 1

            time.sleep(5)

        if not dry_run and cat_added > 0:
            existing_entries.sort(key=lambda e: e.get("name", "").lower())
            save_category(existing_entries, filepath)

        total_added += cat_added
        total_skipped += cat_skipped
        console.print(f"  [bold]{cat}[/bold]: {cat_added} added, {cat_skipped} skipped.")

    console.print(
        f"\n[green]Seed complete[/green]: {total_added} added, {total_skipped} skipped."
    )


# --- SEC EDGAR company seeding ---

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_USER_AGENT = "ReservedNamesBot/1.0 (SNS username protection tool)"

# Normalize SEC exchange names to short readable form
SEC_EXCHANGE_MAP = {
    "NYSE": "NYSE",
    "Nasdaq": "NASDAQ",
    "CBOE": "CBOE",
    "OTC": "OTC",
}


def fetch_sec_companies() -> list[dict]:
    """Fetch all US public company tickers with exchange info from SEC EDGAR."""
    headers = {"User-Agent": SEC_USER_AGENT}
    resp = requests.get(SEC_TICKERS_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # Format: {"fields": ["cik", "name", "ticker", "exchange"], "data": [[...], ...]}
    fields = data["fields"]
    companies = []
    for row in data["data"]:
        entry = dict(zip(fields, row))
        exchange = SEC_EXCHANGE_MAP.get(entry.get("exchange", ""), entry.get("exchange", ""))
        companies.append({
            "cik": entry["cik"],
            "ticker": entry["ticker"],
            "name": entry["name"],
            "exchange": exchange,
        })
    return companies


def titlecase_company(name: str) -> str:
    """Normalize SEC company names to readable form.

    SEC data is inconsistent: "Apple Inc." vs "NVIDIA CORP". We detect
    all-caps names and title-case them, but leave mixed-case names alone
    since they're likely already correct.
    """
    # If the name is already mixed-case (has lowercase), leave it alone.
    # Check the alphabetic characters only.
    alpha = [c for c in name if c.isalpha()]
    upper_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha) if alpha else 0
    if upper_ratio < 0.7:
        return name

    # All-caps name: convert to title case with suffix handling
    suffixes_upper = {"LLC", "LP", "LTD", "INC", "CORP", "CO", "PLC", "AG", "SA",
                      "NV", "SE", "AB", "ASA", "ADR", "ETF", "REIT"}
    words = name.split()
    result = []
    for w in words:
        stripped = w.strip(".,/")
        if stripped in suffixes_upper:
            # Preserve original punctuation around the suffix
            result.append(w)
        else:
            result.append(w.capitalize())
    return " ".join(result)


@cli.command("seed-companies")
@click.option("--dry-run", is_flag=True, help="Show what would be imported without writing.")
@click.option("--exchange", default=None, help="Filter by exchange prefix (not yet implemented, reserved for future).")
def seed_companies(dry_run, exchange):
    """Seed brand entries from SEC EDGAR (US public companies).

    Fetches all NYSE/NASDAQ listed companies and adds them as brand entries.
    Idempotent: skips companies already in brands.yaml.
    Can be re-run to pick up newly listed companies.
    """
    console.print("[cyan]Fetching company list from SEC EDGAR...[/cyan]")
    try:
        companies = fetch_sec_companies()
    except Exception as e:
        console.print(f"[red]Failed to fetch SEC data: {e}[/red]")
        return

    console.print(f"  Got {len(companies)} companies from SEC.")

    existing_entries, filepath = load_category("brand")
    existing_names = {normalize_username(e.get("name", "")) for e in existing_entries}

    added = skipped = 0
    for co in companies:
        name = titlecase_company(co["name"])
        ticker = co["ticker"]

        if normalize_username(name) in existing_names:
            skipped += 1
            continue

        if dry_run:
            console.print(f"  [dim]{name} ({ticker})[/dim]")
            added += 1
            continue

        exchange = co.get("exchange", "")
        stock_val = f"{exchange}:{ticker}" if exchange else ticker
        entry = {
            "name": name,
            "stock_exchange": stock_val,
        }
        existing_entries.append(entry)
        existing_names.add(normalize_username(name))
        added += 1

    if not dry_run and added > 0:
        existing_entries.sort(key=lambda e: e.get("name", "").lower())
        save_category(existing_entries, filepath)

    console.print(f"\n[green]Company seed complete[/green]: {added} added, {skipped} skipped.")


# --- Wikidata trademark/brand seeding ---

def build_trademark_query(min_sitelinks=5, limit=2000):
    """SPARQL query for well-known brand/product names from Wikidata."""
    return f"""
SELECT DISTINCT ?product ?productLabel ?ownerLabel WHERE {{
  VALUES ?type {{ wd:Q431289 wd:Q167270 }}  # brand, trademark
  ?product wdt:P31 ?type .
  ?product wdt:P127 ?owner .
  ?product wikibase:sitelinks ?sitelinks .
  FILTER(?sitelinks >= {min_sitelinks})
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
}}
LIMIT {limit}
"""


@cli.command("seed-trademarks")
@click.option("--dry-run", is_flag=True, help="Show what would be imported without writing.")
@click.option("--min-sitelinks", default=5, help="Minimum Wikipedia sitelinks (proxy for notability).")
@click.option("--limit", default=5000, help="Max results from Wikidata.")
def seed_trademarks(dry_run, min_sitelinks, limit):
    """Seed brand entries from Wikidata (well-known trademarks/brands).

    Queries Wikidata for notable brand names and product trademarks.
    Only includes entries with enough Wikipedia articles to indicate real-world notability.
    Idempotent: skips entries already in brands.yaml.
    """
    console.print("[cyan]Querying Wikidata for notable trademarks/brands...[/cyan]")
    query = build_trademark_query(min_sitelinks=min_sitelinks, limit=limit)

    try:
        results = run_sparql_query(query)
    except Exception as e:
        console.print(f"[red]Query failed: {e}[/red]")
        return

    console.print(f"  Got {len(results)} results.")

    existing_entries, filepath = load_category("brand")
    existing_names = {normalize_username(e.get("name", "")) for e in existing_entries}
    # Also index stripped names so "Tron" matches "Tron Inc."
    existing_stripped = {normalize_username(strip_company_suffix(e.get("name", "")))
                        for e in existing_entries}

    added = skipped = 0
    seen = set()

    for row in results:
        entity_uri = row.get("product", {}).get("value", "")
        qid = entity_uri.rsplit("/", 1)[-1] if entity_uri else None

        name = row.get("productLabel", {}).get("value", "")
        if not name or name == qid or name in seen:
            skipped += 1
            continue
        seen.add(name)

        slug = normalize_username(name)
        if slug in existing_names or slug in existing_stripped:
            skipped += 1
            continue

        owner = row.get("ownerLabel", {}).get("value", "")

        if dry_run:
            console.print(f"  [dim]{name} (owner: {owner})[/dim]")
            added += 1
            continue

        entry = {"name": name}
        if owner:
            entry["description"] = f"Trademark owned by {owner}"
        if qid:
            entry["wikidata_id"] = qid
        existing_entries.append(entry)
        existing_names.add(slug)
        added += 1

    if not dry_run and added > 0:
        existing_entries.sort(key=lambda e: e.get("name", "").lower())
        save_category(existing_entries, filepath)

    console.print(f"\n[green]Trademark seed complete[/green]: {added} added, {skipped} skipped.")


# --- Wikidata organization seeding ---

WIKIDATA_ORG_TYPES = [
    "Q484652",    # international organization
    "Q245065",    # intergovernmental organization
    "Q79913",     # non-governmental organization
    "Q327333",    # government agency
    "Q4358176",   # United Nations specialized agency
    "Q1530022",   # sports governing body
    "Q476028",    # college/university
    "Q3918",      # university
    "Q875538",    # public university
    "Q15936437",  # research university
    "Q23002054",  # private university
]


def build_org_query(org_type_qid, limit=2000):
    """SPARQL query for notable organizations with social handles."""
    optional_extras = []
    select_extras = []
    for platform, prop in WIKIDATA_PLATFORM_PROPS.items():
        if platform in ("twitter", "instagram"):
            continue
        var = f"?{platform}"
        select_extras.append(var)
        optional_extras.append(f"  OPTIONAL {{ ?org wdt:{prop} {var} . }}")

    extras_select = " ".join(select_extras)
    extras_optional = "\n".join(optional_extras)

    return f"""
SELECT ?org ?orgLabel ?orgDescription ?twitter ?instagram {extras_select}
WHERE {{
  ?org wdt:P31/wdt:P279* wd:{org_type_qid} .

  OPTIONAL {{ ?org wdt:P2002 ?twitter . }}
  OPTIONAL {{ ?org wdt:P2003 ?instagram . }}

{extras_optional}

  FILTER(BOUND(?twitter) || BOUND(?instagram))

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
}}
LIMIT {limit}
"""


@cli.command("seed-orgs")
@click.option("--dry-run", is_flag=True, help="Show what would be imported without writing.")
@click.option("--limit", default=2000, help="Max results per org type query.")
def seed_orgs(dry_run, limit):
    """Seed organization entries from Wikidata.

    Queries for international orgs, NGOs, government agencies, sports bodies,
    and universities that have social media presence.
    Idempotent: skips entries already in organizations.yaml.
    """
    existing_entries, filepath = load_category("organization")
    existing_names = {normalize_username(e.get("name", "")) for e in existing_entries}
    existing_wikidata = {e.get("wikidata_id") for e in existing_entries if e.get("wikidata_id")}
    total_added = total_skipped = 0
    seen_qids = set()

    for org_qid in WIKIDATA_ORG_TYPES:
        console.print(f"[cyan]Querying Wikidata for org type {org_qid}...[/cyan]")
        query = build_org_query(org_qid, limit=limit)

        try:
            results = run_sparql_query(query)
        except Exception as e:
            console.print(f"[red]  Query failed: {e}[/red]")
            time.sleep(5)
            continue

        console.print(f"  Got {len(results)} results.")
        added = skipped = 0

        for row in results:
            entity_uri = row.get("org", {}).get("value", "")
            qid = entity_uri.rsplit("/", 1)[-1] if entity_uri else None
            if not qid or qid in seen_qids:
                continue
            seen_qids.add(qid)

            name = row.get("orgLabel", {}).get("value", "")
            if not name or name == qid:
                skipped += 1
                continue

            if qid in existing_wikidata or normalize_username(name) in existing_names:
                skipped += 1
                continue

            description = row.get("orgDescription", {}).get("value")
            handles = {}
            for platform in WIKIDATA_PLATFORM_PROPS:
                val = row.get(platform, {}).get("value")
                if val:
                    handles[platform] = val

            if not handles:
                skipped += 1
                continue

            if dry_run:
                handle_str = ", ".join(f"{p}=@{h}" for p, h in handles.items())
                console.print(f"  [dim]{name}: {handle_str}[/dim]")
                added += 1
                continue

            entry = {"name": name}
            if description:
                entry["description"] = description
            entry["wikidata_id"] = qid
            entry["handles"] = handles
            existing_entries.append(entry)
            existing_names.add(normalize_username(name))
            existing_wikidata.add(qid)
            added += 1

        total_added += added
        total_skipped += skipped
        time.sleep(5)

    if not dry_run and total_added > 0:
        existing_entries.sort(key=lambda e: e.get("name", "").lower())
        save_category(existing_entries, filepath)

    console.print(f"\n[green]Org seed complete[/green]: {total_added} added, {total_skipped} skipped.")


if __name__ == "__main__":
    cli()

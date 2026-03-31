# Reserved Names Database

A database of usernames and names that should be protected when launching a new social network or online service. Prevents impersonation of notable figures and trademark squatting.

## What's in here

YAML files in `data/` are the source of truth. Each file covers one category:

| File | Contents | Count | Source |
|------|----------|-------|--------|
| `celebrities.yaml` | Actors, musicians, athletes, etc. | ~18 | Manual curation |
| `influencers.yaml` | Social media creators | ~10 | Manual curation |
| `politicians.yaml` | Heads of state, elected officials | ~8 | Manual curation |
| `journalists.yaml` | Anchors, commentators, podcasters | ~5 | Manual curation |
| `organizations.yaml` | UN, NASA, FIFA, etc. | ~6 | Manual curation |
| `brands.yaml` | Companies and trademarks | ~8,800 | SEC EDGAR + Wikidata |

All files are sorted alphabetically by name.

## How data was accumulated

### People (celebrities, influencers, politicians, journalists)

Hand-curated entries with verified social media handles. Each entry includes the person's actual usernames on major platforms (Twitter/X, Instagram, YouTube, TikTok, Facebook, Threads, Bluesky, Mastodon). These handles serve as the source of truth for verifying someone's identity if they claim the name.

### Organizations

Hand-curated entries for major international organizations with their official social handles.

### Brands and companies

Three automated sources, all re-runnable:

1. **SEC EDGAR** (`seed-companies`): All ~10,000 US publicly listed companies (NYSE, NASDAQ, CBOE, OTC) with their stock exchange and ticker. Source: `company_tickers_exchange.json` from SEC.

2. **Wikidata trademarks** (`seed-trademarks`): ~700 well-known brand/trademark names from Wikidata, filtered to entries with 5+ Wikipedia articles as a notability threshold.

3. **Manual curation**: Major tech/consumer brands with their social media handles.

## Entry format

```yaml
- name: "Dwayne Johnson"
  description: "Actor and professional wrestler"
  death: "2099-01-01"              # optional, date of death
  stock_exchange: "NYSE:DIS"       # optional, for publicly traded companies
  wikidata_id: "Q10738"            # optional, Wikidata entity ID
  handles:
    twitter: "TheRock"
    instagram: "therock"
    youtube: "therock"
    tiktok: "therock"
    facebook: "DwayneJohnson"
```

**Fields:**
- `name` (required): Display name of the person/entity
- `description` (optional): Short description
- `death` (optional): Date of death, for the platform to decide how long to keep the reservation
- `stock_exchange` (optional): Exchange and ticker, e.g. `NYSE:MSFT`
- `wikidata_id` (optional): For entries seeded from Wikidata
- `handles` (optional): Dict of platform -> username. Only actual usernames the person/entity uses on that platform. No guessing.

## CLI tool

`reserved_names.py` provides commands for managing and querying the data.

### Checking a username

```bash
# Check if a username is taken (matches handles AND company names sans suffixes)
python3 reserved_names.py check therock     # -> RESERVED: "Dwayne Johnson"
python3 reserved_names.py check nvidia      # -> RESERVED: "Nvidia CORP"
python3 reserved_names.py check randomuser  # -> AVAILABLE
```

Exit code 0 = available, 1 = reserved. Company name matching strips common suffixes (Inc, Corp, LLC, Ltd, etc.) so "nvidia" matches "Nvidia CORP".

### Other commands

```bash
python3 reserved_names.py lookup "Taylor Swift"   # Full details for a person
python3 reserved_names.py search "football"        # Search names/descriptions/handles
python3 reserved_names.py verify "Barack Obama"    # Show verification requirements
python3 reserved_names.py stats                    # Database statistics
python3 reserved_names.py export --format csv      # Export to CSV
```

### Adding entries

```bash
python3 reserved_names.py add \
  --name "Someone Famous" \
  --category celebrity \
  --description "Known for something" \
  --twitter "someonefamous" \
  --instagram "someonefamous"
```

### Refreshing automated data

These commands are idempotent and can be re-run at any time:

```bash
python3 reserved_names.py seed              # Refresh people from Wikidata
python3 reserved_names.py seed-companies    # Refresh companies from SEC EDGAR
python3 reserved_names.py seed-trademarks   # Refresh trademarks from Wikidata
```

## Contributing

Contributions are welcome via pull request. When adding entries:

1. Add to the appropriate YAML file in `data/`
2. Only include **actual usernames** the person uses on social platforms — do not guess or invent handles
3. Entries are automatically sorted alphabetically on save, but please keep your additions in alphabetical order for clean diffs
4. For people: include at least one social media handle as a verification source
5. For companies: include `stock_exchange` if publicly traded

### Categories

- `celebrity` — actors, musicians, athletes, public figures
- `influencer` — social media creators, YouTubers, streamers
- `politician` — heads of state, elected officials, diplomats
- `journalist` — anchors, commentators, podcasters
- `brand` — companies, products, trademarks
- `organization` — international bodies, NGOs, government agencies

## Dependencies

```
pip install click rich pyyaml requests
```

## Philosophy

- **Handles only, no guessing**: We track usernames people actually use. No speculative aliases or name variants.
- **Platform decides policy**: We provide the data; the platform decides normalization rules, how long to reserve names of deceased people, etc.
- **Transparent data**: Everything is in YAML files — diffable, editable, reviewable.
- **Re-runnable seeds**: Automated data sources can be refreshed at any time without duplicating entries.

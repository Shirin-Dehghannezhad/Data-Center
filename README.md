# Data Center

Collection of data on data centre facilities in Europe: where they are, who operates them, how large they are, how much power they use and when they opened. The facility list is built from open sources and then enriched with web research and data scraped from datacenters.com.

## Structure

```
Data-Center/
├── scripts/                          notebooks, run from top to bottom
│   ├── datacenter_location.ipynb     1. facility locations from OpenStreetMap and PeeringDB
│   ├── llm_year_area.ipynb           2. opening year and floor area per facility, via Claude with web search
│   └── datacenters_scraper.ipynb     3. facility details scraped from datacenters.com
└── data/
    ├── raw/                          downloads and API answers, never edited by hand
    │   ├── osm_tiles/                OpenStreetMap results, one file per map box
    │   ├── peeringdb/                PeeringDB facility list
    │   ├── llm/<country>/            full Claude answer per facility, with cited sources
    │   └── datacenters_com/          scraper progress files
    └── clean/                        tables produced by the notebooks
        ├── datacenters_locations.csv
        ├── llm_year_area_<country>.csv
        ├── datacenters_austria_merged.csv
        └── eu_datacenters.csv / .json
```

Paths in the notebooks are set relative to the project folder (the one containing `.git`), so they can be run from any folder inside the project.

## Notebooks

### 1. `datacenter_location.ipynb`

Facility locations are collected from OpenStreetMap (objects tagged `telecom=data_center` or `building=data_center`, via the Overpass API) and PeeringDB (facilities registered by their operators). Sites from the two sources closer than 250 m are merged into one. Country, region and nearest town are added from the coordinates, and each site gets a stable ID (`DC000001`, ...).

- Output: `data/clean/datacenters_locations.csv`
- Downloads are saved in `data/raw/`, so later runs read them instead of downloading again.
- An interactive map of all sites, filterable by country, is shown at the end.

### 2. `llm_year_area.ipynb`

For every named facility of one country, Claude searches the web for the opening year and the floor area and has to cite the page each value comes from. A value counts as verified only when the number appears in the cited text.

- Input: `data/clean/datacenters_locations.csv`
- Output: `data/clean/llm_year_area_<country>.csv`, plus the full answers in `data/raw/llm/<country>/`
- Needs an Anthropic API key in the `ANTHROPIC_API_KEY` environment variable.
- The run stops at `MAX_COST_USD` and continues with the remaining sites when run again.

### 3. `datacenters_scraper.ipynb`

datacenters.com is scraped country by country with a headless browser. Each facility page carries its data as a structured object (address, coordinates, operator, facility type, certifications, building and colocation space, total power), which is read directly. The page text is searched as a fallback for opening year, PUE, water use and energy source.

- Output: `data/clean/eu_datacenters.csv` and `.json`, one row per facility, missing values as `N/A`
- Countries are set in the Settings cell. New countries are added to the existing list, and each run continues where the last one stopped.
- A pause of 2 seconds follows every page, so a full EU run takes several hours.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install pandas numpy requests ftfy unidecode reverse_geocoder pycountry folium ipywidgets \
            anthropic playwright beautifulsoup4 lxml
playwright install chromium
```

## Data sources

- **OpenStreetMap**: © OpenStreetMap contributors, available under the Open Database License (ODbL).
- **PeeringDB**: public facility data from peeringdb.com.
- **datacenters.com**: facility pages, subject to the site's terms of use. The scraper follows robots.txt.
- **Web research by Claude**: every value comes with the URL it was taken from, kept in `data/raw/llm/`.

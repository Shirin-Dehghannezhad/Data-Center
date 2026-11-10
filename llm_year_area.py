"""
Opening year and floor area of data centres in one country, found by Claude with web search.

Input:  data/datacenters_locations.csv, the facility list from 01_datacenter_locations.ipynb
Output: data/llm_year_area_<country>.csv, one row per named site
        data/llm_raw/<country>/<dc_id>.json, the full API answer per site, for checking sources later

Each named site is searched on the web by Claude (at most MAX_SEARCHES searches per site).
Claude has to cite the page each value comes from. A value counts as verified only when the
number appears in the text Claude cited from that page; unverified values are kept but flagged.

Run:  python llm_year_area.py
The run stops when MAX_COST_USD is reached and continues where it stopped on the next run.
"""

import json
import os
import re
import time

import anthropic
import pandas as pd

# Settings ---------------------------------------------------------------------

COUNTRY = "Austria"
COUNTRY_CODE = "AT"              
TIMEZONE = "Europe/Vienna"

MODEL = "claude-haiku-5-5"       
MAX_SEARCHES = 3                 
MAX_COST_USD = 10.0           

LOCATIONS_FILE = "data/datacenters_locations.csv"
OUTPUT_FILE = f"data/llm_year_area_{COUNTRY.lower()}.csv"
RAW_DIR = f"data/llm_raw/{COUNTRY.lower()}"

PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
}
PRICE_PER_SEARCH = 0.01

SQ_FT_TO_M2 = 0.092903
TO_M2 = {"m2": 1, "sq_ft": SQ_FT_TO_M2, "hectare": 10_000, "acre": 4_046.86}
CURRENT_YEAR = pd.Timestamp.now().year

INSTRUCTIONS = """You research one specific data centre facility. Search the web, also in the local
language (for Austria and Germany e.g. "Rechenzentrum", "Eröffnung", "Inbetriebnahme", "Quadratmeter").

Find:
1. The year the facility started operating. If only the start of construction or the announcement
   is found, give that year and say so in year_type.
2. The size of the facility: gross building floor area, otherwise IT / data hall ("white space") area,
   otherwise the plot or campus area. Say which one in area_type.

Preferred sources, in this order:
  1. the operator's own publications: company website, press releases, annual or financial reports,
     investor presentations, regulatory filings (e.g. SEC 10-K, stock exchange announcements)
  2. journals, conference papers and public authority documents (e.g. planning permits, energy agency reports)
  3. news articles and trade press
  4. data centre directories and other websites, only if nothing better is found

Rules:
- Only use what the search results say about THIS facility (same operator and location). Other sites
  of the same operator do not count. Expansions are not the opening year.
- First write one short sentence per value found, so that it carries its citation.
- Then end with exactly one JSON object, no nesting, with these keys:
  year (integer or null), year_type ("opened", "construction_started", "announced" or "unknown"),
  year_source_url (string or null), year_source_type (see below),
  area_value (number as written in the source, or null), area_unit ("m2", "sq_ft", "hectare", "acre" or null),
  area_type ("building", "white_space", "land" or "unknown"), area_source_url (string or null),
  area_source_type (see below),
  match_confidence ("high", "medium" or "low": how sure you are the sources are about this facility).
Source types: "company_website", "press_release", "annual_or_financial_report", "investor_presentation",
"regulatory_filing", "journal_or_conference", "public_authority", "news", "directory", "other" or null."""


# Functions --------------------------------------------------------------------

def ask_claude(client, site):
    """Sends one site to Claude with web search and returns the full answer as a dict."""
    place = ", ".join(str(v) for v in [site.address, site.city_latin, site.country] if pd.notna(v))
    question = (f"Facility: {site.name_latin}\n"
                f"Operator: {site.operator_latin if pd.notna(site.operator_latin) else 'unknown'}\n"
                f"Location: {place}\n"
                f"Website: {site.website if pd.notna(site.website) else 'unknown'}")
    messages = [{"role": "user", "content": question}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES,
              "user_location": {"type": "approximate", "country": COUNTRY_CODE, "timezone": TIMEZONE}}]

    content, usage = [], {"input_tokens": 0, "output_tokens": 0, "searches": 0}
    for _ in range(3):   
        response = client.messages.create(model=MODEL, max_tokens=3000, system=INSTRUCTIONS,
                                          messages=messages, tools=tools)
        answer = response.model_dump()
        content += answer["content"]
        usage["input_tokens"] += answer["usage"]["input_tokens"]
        usage["output_tokens"] += answer["usage"]["output_tokens"]
        usage["searches"] += (answer["usage"].get("server_tool_use") or {}).get("web_search_requests", 0)
        if answer["stop_reason"] != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": answer["content"]}]
    return {"content": content, "usage": usage}


def final_json(text):
    objects = re.findall(r"\{[^{}]*\}", text)
    for candidate in reversed(objects):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return {}


def number_in_texts(number, texts):
    if number is None:
        return False
    as_text = f"{number:g}" if isinstance(number, float) else str(number)
    variants = {as_text, as_text.replace(".", ",")}
    for text in texts:
        cleaned = re.sub(r"(?<=\d)[.,\s'](?=\d{3}\b)", "", text)
        if any(re.search(rf"(?<![\d.,]){re.escape(v)}(?![\d])", cleaned) for v in variants):
            return True
    return False


def read_answer(site, answer):
    content = answer["content"]
    text = "".join(block["text"] for block in content if block["type"] == "text")
    values = final_json(text)

    # Text Claude cited, grouped by page. This comes from the search results, not from the model.
    cited = {}
    for block in content:
        for citation in (block.get("citations") or []) if block["type"] == "text" else []:
            cited.setdefault(citation.get("url"), []).append(citation.get("cited_text") or "")

    year = values.get("year")
    if not (isinstance(year, int) and 1960 <= year <= CURRENT_YEAR):
        year = None
    area_value, area_unit = values.get("area_value"), values.get("area_unit")
    area_m2 = round(area_value * TO_M2[area_unit]) if isinstance(area_value, (int, float)) and area_unit in TO_M2 else None

    usage = answer["usage"]
    price = PRICES[MODEL]
    cost = (usage["input_tokens"] * price["input"] + usage["output_tokens"] * price["output"]) / 1e6 \
        + usage["searches"] * PRICE_PER_SEARCH

    return {
        "dc_id": site.dc_id,
        "name": site.name_latin,
        "operator": site.operator_latin,
        "city": site.city_latin,
        "llm_year": year,
        "llm_year_type": values.get("year_type"),
        "llm_year_url": values.get("year_source_url"),
        "llm_year_source_type": values.get("year_source_type"),
        "llm_year_verified": year is not None and number_in_texts(year, cited.get(values.get("year_source_url"), [])),
        "llm_area_m2": area_m2,
        "llm_area_as_written": f"{area_value} {area_unit}" if area_m2 else None,
        "llm_area_type": values.get("area_type"),
        "llm_area_url": values.get("area_source_url"),
        "llm_area_source_type": values.get("area_source_type"),
        "llm_area_verified": area_m2 is not None and number_in_texts(area_value, cited.get(values.get("area_source_url"), [])),
        "match_confidence": values.get("match_confidence"),
        "searches": usage["searches"],
        "cost_usd": round(cost, 4),
        "model": MODEL,
    }


# Run --------------------------------------------------------------------------

if __name__ == "__main__":
    client = anthropic.Anthropic()   
    os.makedirs(RAW_DIR, exist_ok=True)

    sites = pd.read_csv(LOCATIONS_FILE, encoding="utf-8")
    sites = sites[(sites["country"] == COUNTRY) & sites["name_latin"].notna()]
    done = set(pd.read_csv(OUTPUT_FILE, encoding="utf-8")["dc_id"]) if os.path.exists(OUTPUT_FILE) else set()
    spent = pd.read_csv(OUTPUT_FILE, encoding="utf-8")["cost_usd"].sum() if os.path.exists(OUTPUT_FILE) else 0.0
    todo = sites[~sites["dc_id"].isin(done)]
    print(f"{COUNTRY}: {len(sites)} named sites, {len(todo)} to do, ${spent:.2f} spent so far")

    for n, site in enumerate(todo.itertuples(), 1):
        if spent >= MAX_COST_USD:
            print(f"Stopped at the cost limit of ${MAX_COST_USD}")
            break
        try:
            answer = ask_claude(client, site)
        except anthropic.APIError as error:   # the site is retried on the next run
            print(f"{site.dc_id}: {error}")
            time.sleep(10)
            continue

        with open(os.path.join(RAW_DIR, f"{site.dc_id}.json"), "w", encoding="utf-8") as f:
            json.dump(answer, f, ensure_ascii=False, indent=1)
        row = read_answer(site, answer)
        pd.DataFrame([row]).to_csv(OUTPUT_FILE, mode="a", header=not os.path.exists(OUTPUT_FILE),
                                   index=False, encoding="utf-8")
        spent += row["cost_usd"]
        print(f"{n}/{len(todo)} {row['name']}: year {row['llm_year']} ({'verified' if row['llm_year_verified'] else 'unverified'}), "
              f"area {row['llm_area_m2']} m² ({'verified' if row['llm_area_verified'] else 'unverified'}), ${spent:.2f} total")

    if os.path.exists(OUTPUT_FILE):
        results = pd.read_csv(OUTPUT_FILE, encoding="utf-8")
        print(f"\n{len(results)} sites done, ${results['cost_usd'].sum():.2f} spent, "
              f"verified year for {results['llm_year_verified'].sum()}, verified area for {results['llm_area_verified'].sum()}")

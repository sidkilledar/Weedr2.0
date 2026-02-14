from __future__ import annotations

import html
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

# ---- Config ----
RATINGS_CSV = "Rating.csv"
CCR_CSV = "CCR4500.csv"

ENABLE_EBAY = True
ENABLE_ETSY = False  # enable later
ENABLE_AMAZON = False  # enable later (Playwright)

# ---- Helpers ----
def norm(s: Any) -> str:
    return " ".join(str(s).split()).strip().lower()


def safe(s: Any) -> str:
    return html.escape("" if s is None else str(s))


def to_bool_yes_no(x: Any) -> bool:
    v = norm(x)
    return v in ("yes", "y", "true", "1")


# ---- Load CSV Lists ----
def load_lists() -> Tuple[pd.DataFrame, set]:
    ratings = pd.read_csv(RATINGS_CSV)
    ratings.columns = [c.strip() for c in ratings.columns]

    ratings = ratings.rename(
        columns={
            "Scientific Name": "scientific_name",
            "Common Name": "common_name",
            "CDFA Pest Rating": "rating",
            "CCR 4500 Noxious Weeds": "is_ccr_flag",
        }
    )

    ratings = ratings[
        ratings["scientific_name"].astype(str).str.strip().str.lower()
        != "scientific_name"
    ]

    ratings["scientific_name"] = ratings["scientific_name"].astype(str).str.strip()
    ratings["common_name"] = ratings["common_name"].astype(str).str.strip()
    ratings["rating"] = ratings["rating"].astype(str).str.strip().str.upper()
    ratings["is_ccr_flag"] = ratings["is_ccr_flag"].apply(to_bool_yes_no)
    ratings["scientific_name_norm"] = ratings["scientific_name"].apply(norm)

    ccr = pd.read_csv(CCR_CSV)
    ccr.columns = [c.strip() for c in ccr.columns]
    ccr = ccr.rename(
        columns={
            "Scientific Name": "scientific_name",
            "Common Name": "common_name",
        }
    )

    ccr = ccr[
        ccr["scientific_name"].astype(str).str.strip().str.lower()
        != "scientific_name"
    ]

    ccr_set = set(ccr["scientific_name"].apply(norm).tolist())

    return ratings, ccr_set


# ---- Build Scan Queue ----
def build_queue(ratings: pd.DataFrame, limit_items: int) -> List[Dict[str, Any]]:
    priority_map = {"A": 0, "B": 1, "C": 2}
    df = ratings.copy()
    df["priority"] = df["rating"].map(priority_map).fillna(3)
    df = df.sort_values(["priority", "scientific_name"])

    out: List[Dict[str, Any]] = []

    for _, row in df.head(limit_items).iterrows():
        sci = str(row["scientific_name"]).strip()
        com = str(row["common_name"]).strip()
        query = f"{sci} {com}".strip()

        out.append(
            {
                "scientific_name": sci,
                "common_name": com,
                "rating": str(row["rating"]).strip().upper(),
                "is_ccr_from_ratings": bool(row["is_ccr_flag"]),
                "query": query,
            }
        )

    return out


# ---- Marketplace Scrapers ----
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    )
}


def ebay_search(query: str, limit_results: int) -> List[Dict[str, str]]:
    q = "+".join(query.split())
    url = f"https://www.ebay.com/sch/i.html?_nkw={q}"

    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    results: List[Dict[str, str]] = []

    for li in soup.select("li.s-item"):
        a = li.select_one("a.s-item__link")
        title_el = li.select_one(".s-item__title")
        if not a or not title_el:
            continue

        title = title_el.get_text(" ", strip=True)
        if not title or title.lower() == "shop on ebay":
            continue

        href = a.get("href", "")
        price_el = li.select_one(".s-item__price")
        price = price_el.get_text(" ", strip=True) if price_el else ""

        results.append(
            {
                "site": "eBay",
                "title": title,
                "price": price,
                "url": href,
            }
        )

        if len(results) >= limit_results:
            break

    return results


def etsy_search(query: str, limit_results: int) -> List[Dict[str, str]]:
    q = "+".join(query.split())
    url = f"https://www.etsy.com/search?q={q}"

    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    results: List[Dict[str, str]] = []

    for a in soup.select("a.listing-link, a.wt-text-link"):
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not href.startswith("http"):
            continue
        if not title:
            continue

        results.append({"site": "Etsy", "title": title, "price": "", "url": href})

        if len(results) >= limit_results:
            break

    return results


# ---- FastAPI App ----
app = FastAPI()

STATE: Dict[str, Any] = {
    "running": False,
    "progress": "",
    "results": [],
    "error": None,
    "last_run_at": None,
}


@dataclass
class ScanConfig:
    limit_items: int = 10
    per_site_results: int = 3
    throttle_s: float = 0.8


class RunRequest(BaseModel):
    limit_items: int = 10
    per_site_results: int = 3


def do_scan(cfg: ScanConfig) -> List[Dict[str, Any]]:
    ratings, ccr_set = load_lists()
    queue = build_queue(ratings, cfg.limit_items)

    out: List[Dict[str, Any]] = []
    total = len(queue)

    STATE["progress"] = f"Scanning {total} species..."

    for i, item in enumerate(queue, start=1):
        sci = item["scientific_name"]
        com = item["common_name"]
        rating = item["rating"]
        query = item["query"]

        is_ccr = (
            bool(item["is_ccr_from_ratings"])
            or (norm(sci) in ccr_set)
        )

        STATE["progress"] = f"[{i}/{total}] Searching: {query}"

        site_hits = []

        if ENABLE_EBAY:
            try:
                site_hits += ebay_search(query, cfg.per_site_results)
            except Exception as e:
                STATE["error"] = f"eBay search failed for '{query}': {repr(e)}"

        if ENABLE_ETSY:
            try:
                site_hits += etsy_search(query, cfg.per_site_results)
            except Exception as e:
                STATE["error"] = f"Etsy search failed for '{query}': {repr(e)}"

        for h in site_hits:
            out.append(
                {
                    "scientific_name": sci,
                    "common_name": com,
                    "rating": rating,
                    "is_ccr": is_ccr,
                    "site": h["site"],
                    "title": h["title"],
                    "price": h.get("price", ""),
                    "url": h["url"],
                }
            )

        time.sleep(cfg.throttle_s)

    STATE["progress"] = f"Done. Found {len(out)} listings."
    return out


def render_page() -> str:
    results = STATE["results"] or []

    rows = []
    for r in results:
        rows.append(
            f"""
            <tr>
              <td>{safe(r.get("scientific_name",""))}</td>
              <td>{safe(r.get("common_name",""))}</td>
              <td>{safe(r.get("rating",""))}</td>
              <td>{"✅" if r.get("is_ccr") else ""}</td>
              <td>{safe(r.get("site",""))}</td>
              <td>{safe(r.get("title",""))}</td>
              <td>{safe(r.get("price",""))}</td>
              <td><a href="{safe(r.get("url",""))}" target="_blank">open</a></td>
            </tr>
            """
        )

    return f"""
    <html>
    <body>
    <h1>Weedr Live Report</h1>
    <p>Status: {"RUNNING" if STATE["running"] else "IDLE"}</p>
    <p>Progress: {safe(STATE["progress"])}</p>
    <p>Last Run: {safe(STATE["last_run_at"])}</p>
    {f'<p style="color:red;">Error: {safe(STATE["error"])}</p>' if STATE["error"] else ""}

    <form method="post" action="/run">
        <button type="submit">Run Scan</button>
    </form>

    <table border="1">
    <tr>
        <th>Scientific</th>
        <th>Common</th>
        <th>Rating</th>
        <th>CCR</th>
        <th>Site</th>
        <th>Title</th>
        <th>Price</th>
        <th>URL</th>
    </tr>
    {''.join(rows)}
    </table>
    </body>
    </html>
    """


@app.get("/", response_class=HTMLResponse)
def home():
    return render_page()


@app.post("/run")
def run(req: RunRequest = RunRequest()):
    if STATE["running"]:
        return RedirectResponse("/", status_code=303)

    cfg = ScanConfig(
        limit_items=req.limit_items,
        per_site_results=req.per_site_results,
    )

    def worker():
        STATE["running"] = True
        STATE["error"] = None
        STATE["results"] = []
        STATE["progress"] = "Starting..."

        try:
            results = do_scan(cfg)
            STATE["results"] = results
            STATE["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            STATE["error"] = repr(e)
        finally:
            STATE["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return RedirectResponse("/", status_code=303)
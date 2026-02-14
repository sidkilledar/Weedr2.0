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

ENABLE_ETSY = False  # keep False for reliability

def norm(s: Any) -> str:
    return " ".join(str(s).split()).strip().lower()


def safe(s: Any) -> str:
    return html.escape("" if s is None else str(s))


def to_bool_yes_no(x: Any) -> bool:
    v = norm(x)
    return v in ("yes", "y", "true", "1")
  
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
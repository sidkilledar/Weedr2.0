from flask import Flask, request, render_template
from selenium import webdriver
from selenium.webdriver.common.by import By
import pandas as pd
import time

app = Flask(__name__)

# -----------------------------------
# HARD-CODED PAGES TO CHECK (edit these)
# Put the actual product/search pages you want the demo to check
# -----------------------------------
PAGES = [
    "https://www.ebay.com/sch/i.html?_nkw=alligator+weed&_sacat=0&_from=R40&_trksid=p4624852.m570.l1313",
    "https://www.amazon.com/s?k=alligator+weed&crid=1U94XJCUR1F1I&sprefix=alligator+wee%2Caps%2C182&ref=nb_sb_noss_2",
  
]

# -----------------------------------
# Robust CSV Reader (handles tabs)
# -----------------------------------
def read_table(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, engine="python", on_bad_lines="skip")
    if len(df.columns) == 1 and "\t" in df.columns[0]:
        df = pd.read_csv(path, sep="\t", engine="python", on_bad_lines="skip")
    if len(df.columns) == 1:
        df = pd.read_csv(path, sep=None, engine="python", on_bad_lines="skip")
    df.columns = [c.strip() for c in df.columns]
    return df

def load_data():
    ratings = read_table("Rating.csv")
    ratings = ratings.rename(columns={
        "Scientific Name": "scientific_name",
        "Scientific name": "scientific_name",
        "Common Name": "common_name",
        "Common Name(s)": "common_name",
        "Common name(s)": "common_name",
        "CDFA Pest Rating": "rating",
    })
    if "common_name" not in ratings.columns:
        raise ValueError(f"Rating.csv missing Common Name column. Found: {list(ratings.columns)}")
    if "scientific_name" not in ratings.columns:
        ratings["scientific_name"] = ""
    if "rating" not in ratings.columns:
        ratings["rating"] = "N/A"

    ratings["common_name"] = ratings["common_name"].astype(str).str.strip()
    ratings["scientific_name"] = ratings["scientific_name"].astype(str).str.strip()
    ratings["rating"] = ratings["rating"].astype(str).str.strip()

    ccr = read_table("CCR4500.csv")
    ccr = ccr.rename(columns={
        "Scientific Name": "scientific_name",
        "Scientific name": "scientific_name",
    })
    if "scientific_name" not in ccr.columns:
        raise ValueError(f"CCR4500.csv missing Scientific Name column. Found: {list(ccr.columns)}")

    ccr_set = set(ccr["scientific_name"].astype(str).str.strip().str.lower())
    return ratings, ccr_set

RATINGS_DF, CCR_SET = load_data()

# -----------------------------------
# Selenium page fetch (like your old code)
# -----------------------------------
def get_page_text(url: str) -> str:
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)
    driver.get(url)
    time.sleep(2)  # keep 2 sec for reliability

    text = driver.find_element(By.TAG_NAME, "body").text
    driver.quit()
    return text

# -----------------------------------
# Main Route
# -----------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    query = ""
    result = None
    checks = []
    error = None

    if request.method == "POST":
        query = (request.form.get("name") or "").strip()

        if not query:
            error = "Please enter a plant name."
            return render_template("index.html", query=query, result=result, checks=checks, error=error, pages=PAGES)

        # Lookup in your database (exact match, fastest)
        hit = RATINGS_DF[RATINGS_DF["common_name"].str.lower() == query.lower()]

        sci = ""
        rating = "N/A"
        is_ccr = False
        found_in_db = False

        if not hit.empty:
            row = hit.iloc[0]
            found_in_db = True
            sci = row.get("scientific_name", "")
            rating = row.get("rating", "N/A")
            is_ccr = sci.strip().lower() in CCR_SET if sci else False

        # Check presence across the hardcoded pages
        for url in PAGES:
            try:
                page_text = get_page_text(url)
                present = query.lower() in page_text.lower()
                checks.append({
                    "url": url,
                    "present": present,
                    "status": "OK",
                })
            except Exception as e:
                checks.append({
                    "url": url,
                    "present": False,
                    "status": f"ERROR: {type(e).__name__}",
                })

        result = {
            "common_name": query,
            "scientific_name": sci,
            "rating": rating,
            "is_ccr": is_ccr,
            "found_in_db": found_in_db,
        }

    return render_template("index.html", query=query, result=result, checks=checks, error=error, pages=PAGES)


if __name__ == "__main__":
    app.run(debug=True)
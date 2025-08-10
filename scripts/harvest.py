# scripts/harvest.py
import json, time, os, sys
from urllib.parse import quote
import requests
from datetime import date

WORDS_PATH = "words.json"
LIMIT_PER_RUN = int(os.getenv("LIMIT", "50"))
LANG = os.getenv("LANG", "ja")

def load_words():
    if os.path.exists(WORDS_PATH):
        with open(WORDS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_words(words):
    with open(WORDS_PATH, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=2)

def slugify(s):
    import re
    return re.sub(r"[^a-z0-9一-龠ぁ-んァ-ン]+", "-", s.lower())

def fetch_category_members(category, cmcontinue=None):
    url = "https://ja.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": "500",
        "format": "json"
    }
    if cmcontinue:
        params["cmcontinue"] = cmcontinue
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    titles = [m["title"] for m in data["query"]["categorymembers"] if m.get("ns") == 0]
    nxt = data.get("continue", {}).get("cmcontinue")
    return titles, nxt

def fetch_summary(title, lang="ja"):
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title, safe='')}"
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        return None
    s = r.json()
    if s.get("type") == "disambiguation":
        return None
    page_url = (s.get("content_urls", {}).get("desktop", {}) or {}).get("page", "")
    extract = s.get("extract", "").strip()
    if not extract:
        return None
    # 簡易な非ITフィルタ（音楽・海運などを粗く弾く）
    bad_words = ["交響曲","楽器","作曲","小説","漫画","映画","貨物","海運","船舶"]
    if any(w in extract for w in bad_words):
        return None
    return {
        "term": s.get("title") or title,
        "definition_ja": extract if lang == "ja" else "",
        "definition_en": extract if lang == "en" else "",
        "tags": ["未分類"],  # 後で付け替え
        "difficulty": 1,
        "related_terms": [],
        "examples": [],
        "official_links": [{"title": f"{title} - Wikipedia", "url": page_url}] if page_url else [],
        "source_urls": [page_url] if page_url else [],
        "license": "CC BY-SA",
        "attribution": "Wikipedia",
        "lang": lang,
        "srs": {"next_review": str(date.today()), "interval_days": 0, "stability": 0}
    }

def main():
    # 例：環境変数 CATEGORIES="プログラミング言語,オペレーティングシステム,データベース"
    categories = os.getenv("CATEGORIES", "").split(",")
    categories = [c.strip() for c in categories if c.strip()]
    if not categories:
        print("No CATEGORIES provided."); sys.exit(0)

    words = load_words()
    have = {slugify(w["term"]): True for w in words}
    added = 0

    for cat in categories:
        cont = None
        while True:
            titles, cont = fetch_category_members(cat, cont)
            for t in titles:
                if added >= LIMIT_PER_RUN:
                    save_words(words)
                    print(f"Reached limit ({LIMIT_PER_RUN}). Added={added}")
                    return
                sg = slugify(t)
                if sg in have:
                    continue
                s = fetch_summary(t, lang=LANG)
                time.sleep(0.2)  # rate limit
                if not s:
                    continue
                s["id"] = (max([w.get("id", 0) for w in words]) + 1) if words else 1
                words.append(s)
                have[sg] = True
                added += 1
            if not cont:
                break

    save_words(words)
    print(f"Done. Added={added}")

if __name__ == "__main__":
    main()
# ↑ファイル先頭のimportに追加
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def make_session():
    s = requests.Session()
    # Wikipediaのエチケット：連絡先かリポジトリURLを入れる
    s.headers.update({"User-Agent": "it-terms-harvester/0.1 (+https://github.com/<YOUR_USER>/<YOUR_REPO>)"})
    retry = Retry(
        total=5,
        backoff_factor=1.2,                   # 429/5xxで指数バックオフ
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

SESSION = make_session()
SLEEP = float(os.getenv("SLEEP", "0.8"))     # 1リクエストごと待つ（秒）

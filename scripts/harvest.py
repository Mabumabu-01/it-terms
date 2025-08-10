# scripts/harvest.py
import os
import sys
import re
import json
import time
from datetime import date
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ========= 設定 =========
WORDS_PATH = "words.json"
LIMIT_PER_RUN = int(os.getenv("LIMIT", "50"))   # 1回の最大追加語数（YAMLから上書き可）
LANG = os.getenv("LANG", "ja")                  # 取得言語
SLEEP = float(os.getenv("SLEEP", "0.8"))        # 1リクエストごとの待機秒
UA = "it-terms-harvester/0.1 (+https://github.com/Mabumabu-01/it-terms)"
# ========================

def make_session() -> requests.Session:
    """Wikipedia向けにUser-Agentとリトライを設定したセッションを返す"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    retry = Retry(
        total=5,
        backoff_factor=1.2,  # 429/5xx 時に指数バックオフ
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

SESSION = make_session()

def load_words():
    if os.path.exists(WORDS_PATH):
        with open(WORDS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_words(words):
    with open(WORDS_PATH, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=2)

def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9一-龠ぁ-んァ-ン]+", "-", s.lower())

def fetch_category_members(category: str, cmcontinue: str | None = None):
    """カテゴリから記事タイトル一覧を取る（ゆっくり＆リトライつき）"""
    url = "https://ja.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": "100",      # 取り過ぎ防止（必要なら50まで下げてもOK）
        "format": "json",
        "formatversion": "2",
        "origin": "*",
    }
    if cmcontinue:
        params["cmcontinue"] = cmcontinue

    r = SESSION.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    titles = [m["title"] for m in data["query"]["categorymembers"] if m.get("ns") == 0]
    nxt = data.get("continue", {}).get("cmcontinue")
    time.sleep(SLEEP)  # レート制限に優しく
    return titles, nxt

def fetch_summary(title: str, lang: str = "ja"):
    """要約をREST APIから取得。曖昧ページ/非ITっぽいものは除外"""
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title, safe='')}"
    r = SESSION.get(url, timeout=30)
    if r.status_code != 200:
        return None
    s = r.json()

    # 曖昧ページを除外
    if s.get("type") == "disambiguation":
        return None

    page_url = (s.get("content_urls", {}).get("desktop", {}) or {}).get("page", "")
    extract = (s.get("extract") or "").strip()
    if not extract:
        return None

    # 非ITっぽい要約の簡易フィルタ（必要に応じて調整）
    bad_words = ["交響曲", "楽器", "作曲", "小説", "漫画", "映画", "貨物", "海運", "船舶"]
    if any(w in extract for w in bad_words):
        return None

    time.sleep(SLEEP)  # レート制限に優しく
    return {
        "term": s.get("title") or title,
        "definition_ja": extract if lang == "ja" else "",
        "definition_en": extract if lang == "en" else "",
        "tags": ["未分類"],   # 後で置き換え/整備
        "difficulty": 1,
        "related_terms": [],
        "examples": [],
        "official_links": ([{"title": f"{title} - Wikipedia", "url": page_url}] if page_url else []),
        "source_urls": ([page_url] if page_url else []),
        "license": "CC BY-SA",
        "attribution": "Wikipedia",
        "lang": lang,
        "srs": {"next_review": str(date.today()), "interval_days": 0, "stability": 0},
    }

def main():
    # 例：CATEGORIES="プログラミング言語,オペレーティングシステム,データベース"
    categories = [c.strip() for c in os.getenv("CATEGORIES", "").split(",") if c.strip()]
    if not categories:
        print("No CATEGORIES provided.")
        sys.exit(0)

    words = load_words()
    have = {slugify(w["term"]): True for w in words}
    next_id = (max([w.get("id", 0) for w in words]) + 1) if words else 1
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
                if not s:
                    continue

                s["id"] = next_id
                words.append(s)
                have[sg] = True
                next_id += 1
                added += 1

            if not cont:
                break

    save_words(words)
    print(f"Done. Added={added}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Arvopaperihaku sovellukselle. Sovellus lähettää salatun hakusanan GitHub Actionsille
(search.yml), tämä skripti hakee osumat Yahoo Financesta ja tallentaa ne salattuna
tiedostoon search/result.enc. Työnkulku vie tiedoston erilliseen search-results-haaraan
yhdeksi commitiksi, joten hakuhistoriaa ei kerry. Hakusanaa ja tuloksia ei kirjoiteta lokiin.
"""
import json
import os
import sys
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, Crypto, log, quiet  # noqa: E402

TYPES = {"EQUITY": "Osake", "ETF": "ETF", "MUTUALFUND": "Rahasto", "INDEX": "Indeksi",
         "CRYPTOCURRENCY": "Krypto", "CURRENCY": "Valuutta", "FUTURE": "Johdannainen"}


def main():
    crypto = Crypto(os.environ.get("SALKKU_PASSWORD", ""))
    try:
        env = json.loads(os.environ["QUERY"])
        req = crypto.decrypt(env)
    except Exception as e:
        log(f"VIRHE: hakupyyntöä ei voitu purkaa ({e.__class__.__name__}) – tarkista SALKKU_PASSWORD")
        sys.exit(1)

    result = {"id": req.get("id"), "results": [], "error": None}
    try:
        with quiet():
            quotes = yf.Search(str(req.get("q", ""))[:80], max_results=20, news_count=0).quotes or []
        for q in quotes:
            if not q.get("symbol"):
                continue
            result["results"].append({
                "symbol": q["symbol"],
                "name": q.get("longname") or q.get("shortname") or q["symbol"],
                "exchange": q.get("exchDisp") or q.get("exchange") or "",
                "type": q.get("quoteType") or "",
                "typeDisp": TYPES.get(q.get("quoteType"), q.get("typeDisp") or ""),
            })
    except Exception as e:
        result["error"] = f"Haku epäonnistui ({e.__class__.__name__}). Yritä hetken päästä uudelleen."

    out = ROOT / "search" / "result.enc"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(crypto.encrypt(result, env.get("salt"))) + "\n", "utf-8")
    log("Haku valmis")


if __name__ == "__main__":
    main()

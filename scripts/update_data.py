#!/usr/bin/env python3
"""
Purkaa salatun seurantalistan (symbols.enc), hakee kurssit Yahoo Financesta
(yfinance), laskee ostotutkan signaalit, lähettää ilmoitukset ja kirjoittaa kaiken
salattuna tiedostoon data/quotes.enc, jonka sovellus purkaa samalla salasanalla.

Salasana luetaan ympäristömuuttujasta SALKKU_PASSWORD (GitHubin salaisuus).
Lokiin ei kirjoiteta kohteiden nimiä, tunnuksia eikä määrää, koska julkisen
repositorion Actions-lokit näkyvät kaikille.

Jokaisesta kohteesta tallennetaan:
  d1      viimeisin kaupankäyntipäivä 5 min välein   (1D)
  d5      5 päivää 30 min välein                      (5D)
  daily   n. 2 v päätöskurssit: c = kurssi, a = osinko-oikaistu (1M–1Y, signaalit)
  weekly  koko historia, viikon viimeinen päätöskurssi (5Y, All)
  sig     ostotutkan signaali, hist = signaalin aiempi osuvuus, rel = vertailu indeksiin
Koko historia haetaan tunnin välein; muuten päivitetään vain päivänsisäinen data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from cryptography.exceptions import InvalidTag

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402
import signals  # noqa: E402
from common import ROOT, Crypto, log, quiet  # noqa: E402

DAILY_BARS = 530
WEEKLY_MAX_POINTS = 900
FULL_EVERY = 3600
BENCH = {".HE": ("^OMXH25", "OMXH25"), ".CO": ("^OMXC25", "OMXC25"), ".ST": ("^OMX", "OMXS30"),
         ".DE": ("^GDAXI", "DAX"), ".OL": ("OBX.OL", "OBX"), "": ("^GSPC", "S&P 500")}


def item_id(item: dict) -> str:
    return (item.get("symbol") or item.get("isin") or "").strip().upper()


def rnd(v: float) -> float:
    a = abs(v)
    return round(v, 2) if a >= 1000 else round(v, 4) if a >= 1 else round(v, 6)


def ser(s: pd.Series | None) -> dict | None:
    """Series -> {"t": [epoch-sekunnit], "c": [arvot]}"""
    if s is None:
        return None
    s = s.dropna()
    if s.empty:
        return None
    return {"t": [int(ts.timestamp()) for ts in s.index], "c": [rnd(float(v)) for v in s.values]}


def thin(s: dict | None, max_points: int) -> dict | None:
    if not s or len(s["t"]) <= max_points:
        return s
    step = math.ceil(len(s["t"]) / max_points)
    idx = list(range(0, len(s["t"]), step))
    if idx[-1] != len(s["t"]) - 1:
        idx.append(len(s["t"]) - 1)
    return {"t": [s["t"][i] for i in idx], "c": [s["c"][i] for i in idx]}


def history(ticker, **kw) -> pd.DataFrame:
    last = None
    for attempt in range(3):
        try:
            return ticker.history(auto_adjust=False, actions=False, **kw)
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def resolve_symbol(item: dict, cache: dict) -> str:
    """Yahoo-tunnus: 'yahoo'-kenttä > 'symbol' > ISIN-haku Yahoosta."""
    if item.get("yahoo"):
        return item["yahoo"].strip()
    if item.get("symbol"):
        return item["symbol"].strip().upper()
    isin = (item.get("isin") or "").strip().upper()
    if not isin:
        raise ValueError("kohteelta puuttuu symbol tai isin")
    if isin in cache:
        return cache[isin]
    with quiet():
        quotes = yf.Search(isin, max_results=6, news_count=0).quotes or []
    if not quotes:
        raise LookupError(f"ISIN {isin} ei löytynyt Yahoo Financesta – lisää kohde haulla tai Yahoo-tunnuksella")
    quotes.sort(key=lambda q: 0 if q.get("quoteType") in ("MUTUALFUND", "ETF") else 1)
    cache[isin] = quotes[0]["symbol"]
    return cache[isin]


def epoch(v) -> int | None:
    """Aikaleima sekunteina: yfinance palauttaa ajat versiosta riippuen lukuna tai pandas-aikaleimana."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, dt.datetime):  # myös pandas.Timestamp
        return int(v.timestamp())
    try:
        return int(pd.Timestamp(v).timestamp())
    except Exception:
        return None


def meta_of(t) -> dict:
    """Kopio kurssihaun metatiedoista. yfinance 1.7+: history_metadata on laiska sanakirja, jonka
    'tradingPeriods'-avain tekee lisähaun ja kaatuu rahastoilla – se ohitetaan."""
    try:
        md = t.history_metadata
    except Exception:
        return {}
    if not md:
        return {}
    raw = getattr(md, "_metadata", None)
    if callable(raw):
        try:
            return {k: v for k, v in dict(raw()).items() if k != "tradingPeriods"}
        except Exception:
            pass
    out = {}
    for k in list(md.keys()):
        if k == "tradingPeriods":
            continue
        try:
            out[k] = md[k]
        except Exception:
            pass
    return out


def _trading_period(meta: dict):
    reg = ((meta.get("currentTradingPeriod") or {}).get("regular") or {})
    a, b = epoch(reg.get("start")), epoch(reg.get("end"))
    return [a, b] if a and b else None


def _bench(sym: str, qtype: str, cache: dict):
    if qtype != "EQUITY":
        return None
    suffix = "." + sym.rsplit(".", 1)[1] if "." in sym else ""
    if suffix not in BENCH:
        return None
    bsym, bname = BENCH[suffix]
    if bsym not in cache:
        try:
            cache[bsym] = history(yf.Ticker(bsym), period="6mo", interval="1d")["Close"].dropna()
        except Exception:
            cache[bsym] = None
    s = cache[bsym]
    if s is None or len(s) <= signals.HORIZON:
        return None
    return bname, float(s.iloc[-1] / s.iloc[-1 - signals.HORIZON] - 1)


def _earnings(t, qtype: str):
    if qtype != "EQUITY":
        return None
    try:
        dates = (t.calendar or {}).get("Earnings Date") or []
        today = dt.date.today()
        dates = [d.date() if isinstance(d, dt.datetime) else d for d in dates]
        future = sorted(d for d in dates if isinstance(d, dt.date) and d >= today)
        return future[0].isoformat() if future else None
    except Exception:
        return None


def fetch(sym: str, bench_cache: dict) -> dict:
    """Täysi haku: koko historia, päivänsisäinen data, signaalihistoria, tulospäivä, indeksivertailu."""
    t = yf.Ticker(sym)
    daily_df = history(t, period="max", interval="1d")
    if daily_df is None or daily_df.empty:
        raise LookupError(f"{sym}: Yahoo ei palauttanut kurssidataa")
    meta = meta_of(t)
    close = daily_df["Close"].dropna()
    adj = (daily_df["Adj Close"] if "Adj Close" in daily_df else daily_df["Close"]).reindex(close.index).fillna(close)

    tail = close.tail(DAILY_BARS)
    daily = ser(tail)
    daily["a"] = [rnd(float(v)) for v in adj.reindex(tail.index).values]
    # Viikkosarja: jokaisen viikon viimeinen todellinen kaupankäyntipäivä (ei tulevaa perjantaita)
    weekly = thin(ser(close.groupby(pd.Grouper(freq="W-FRI")).tail(1)), WEEKLY_MAX_POINTS)

    d1 = d5 = None
    prev_close, tp = None, _trading_period(meta)
    try:
        d1_df = history(t, period="1d", interval="5m")
        m1 = meta_of(t)
        meta = {**meta, **{k: v for k, v in m1.items() if v is not None}}
        prev_close = m1.get("chartPreviousClose") or m1.get("previousClose")
        tp = _trading_period(m1) or tp
        d1 = ser(d1_df["Close"]) if not d1_df.empty else None
        d5_df = history(t, period="5d", interval="30m")
        d5 = ser(d5_df["Close"]) if not d5_df.empty else None
    except Exception:
        pass  # rahastoilla ei ole päivänsisäistä dataa
    if d1 and len(d1["t"]) < 4:
        d1 = None
    if d5 and len(d5["t"]) < 6:
        d5 = None

    closes = daily["c"]
    price = meta.get("regularMarketPrice") or closes[-1]
    if not prev_close or not d1:
        prev_close = closes[-2] if len(closes) > 1 else closes[-1]
    if not d1:
        price = closes[-1]

    qtype = meta.get("instrumentType") or ""
    rel = None
    b = _bench(sym, qtype, bench_cache)
    if b and len(adj) > signals.HORIZON:
        rel = {"name": b[0], "bench": round(b[1], 4), "item": round(float(adj.iloc[-1] / adj.iloc[-1 - signals.HORIZON] - 1), 4)}

    return {
        "symbol": sym,
        "yahooName": meta.get("longName") or meta.get("shortName") or sym,
        "currency": meta.get("currency") or "",
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        "type": qtype,
        "tz": meta.get("exchangeTimezoneName") or "",
        "dp": int(meta["priceHint"]) if isinstance(meta.get("priceHint"), (int, float)) else None,
        "tp": tp,
        "price": rnd(float(price)),
        "prevClose": rnd(float(prev_close)),
        "time": epoch(meta.get("regularMarketTime")) or daily["t"][-1],
        "d1": d1, "d5": d5, "daily": daily, "weekly": weekly,
        "hist": signals.track_record(signals.indicators(adj)),
        "earn": _earnings(t, qtype),
        "rel": rel,
        "fullAt": int(time.time()),
    }


def _zone(tz: str):
    try:
        return ZoneInfo(tz) if tz else dt.timezone.utc
    except Exception:
        return dt.timezone.utc


def _patch(series: dict, ts: int, price: float, tz: str, weekly: bool) -> dict:
    """Päivittää sarjan viimeisen pisteen uuteen kurssiin tai lisää uuden päivän/viikon."""
    s = {k: list(v) for k, v in series.items()}
    z = _zone(tz)
    last = dt.datetime.fromtimestamp(s["t"][-1], z).date()
    new = dt.datetime.fromtimestamp(ts, z).date()
    midnight = int(dt.datetime.combine(new, dt.time(), z).timestamp())
    ratio = (s["a"][-1] / s["c"][-1]) if s.get("a") and s["c"][-1] else 1.0
    key = (lambda d: d.isocalendar()[:2]) if weekly else (lambda d: d)
    if key(new) == key(last):
        s["c"][-1] = rnd(price)
        if weekly:
            s["t"][-1] = max(s["t"][-1], midnight)
        if "a" in s:
            s["a"][-1] = rnd(price * ratio)
    elif new > last:
        s["t"].append(midnight)
        s["c"].append(rnd(price))
        if "a" in s:
            s["a"].append(rnd(price * ratio))
        if not weekly and len(s["t"]) > DAILY_BARS:
            s = {k: v[1:] for k, v in s.items()}
    return s


def fetch_light(sym: str, prev: dict, bench_cache: dict) -> dict:
    """Nopea päivitys: vain päivänsisäinen data; päivä- ja viikkosarjan viimeinen piste päivitetään."""
    out = {k: v for k, v in prev.items() if k != "stale"}
    if not prev.get("d1"):
        return out  # rahasto: arvo päivittyy täydessä haussa
    t = yf.Ticker(sym)
    d1_df = history(t, period="1d", interval="5m")
    m1 = meta_of(t)
    d1 = ser(d1_df["Close"]) if not d1_df.empty else None
    if not d1 or len(d1["t"]) < 4:
        return fetch(sym, bench_cache)
    d5_df = history(t, period="5d", interval="30m")
    d5 = ser(d5_df["Close"]) if not d5_df.empty else None
    price = float(m1.get("regularMarketPrice") or d1["c"][-1])
    ts = epoch(m1.get("regularMarketTime")) or d1["t"][-1]
    tz = prev.get("tz") or m1.get("exchangeTimezoneName") or ""
    out.update({
        "price": rnd(price),
        "prevClose": rnd(float(m1.get("chartPreviousClose") or m1.get("previousClose") or prev["prevClose"])),
        "time": ts, "d1": d1, "tp": _trading_period(m1) or prev.get("tp"),
        "dp": int(m1["priceHint"]) if isinstance(m1.get("priceHint"), (int, float)) else prev.get("dp"),
        "d5": d5 if d5 and len(d5["t"]) >= 6 else prev.get("d5"),
        "daily": _patch(prev["daily"], ts, price, tz, weekly=False),
        "weekly": _patch(prev["weekly"], ts, price, tz, weekly=True) if prev.get("weekly") else None,
    })
    return out


def fetch_fx(currencies: set, prev_fx: dict) -> dict:
    """Valuuttakurssit: montako yksikköä valuuttaa saa eurolla."""
    fx = {}
    for cur in sorted(currencies - {"EUR", ""}):
        base = "GBP" if cur == "GBp" else cur
        try:
            with quiet():
                s = history(yf.Ticker(f"EUR{base}=X"), period="5d", interval="1d")["Close"].dropna()
            fx[cur] = round(float(s.iloc[-1]) * (100 if cur == "GBp" else 1), 6)
        except Exception:
            if cur in prev_fx:
                fx[cur] = prev_fx[cur]
    return fx


def portfolio(config: dict, items: dict, fx: dict) -> dict | None:
    value = day = cost = 0.0
    n = 0
    for it in config.get("items", []):
        d = items.get(item_id(it))
        qty = it.get("qty")
        if not d or not qty:
            continue
        rate = 1.0 if d.get("currency") in ("EUR", "") else fx.get(d["currency"])
        if not rate:
            continue
        value += qty * d["price"] / rate
        day += qty * (d["price"] - d["prevClose"]) / rate
        if it.get("cost"):
            cost += qty * it["cost"] / rate
        n += 1
    if not n:
        return None
    return {"value": round(value, 2), "dayEur": round(day, 2), "day": round(day / (value - day), 5) if value - day else 0,
            "cost": round(cost, 2) if cost else None}


def load_previous(src: str | None, crypto: Crypto) -> tuple[dict, str | None]:
    """Edellinen julkaistu data ja sen suola."""
    if not src:
        return {}, None
    try:
        if src.startswith("http"):
            sep = "&" if "?" in src else "?"
            req = urllib.request.Request(f"{src}{sep}t={int(time.time())}", headers={"User-Agent": "salkku"})
            with urllib.request.urlopen(req, timeout=20) as r:
                env = json.load(r)
        else:
            p = Path(src)
            if not p.exists():
                return {}, None
            env = json.loads(p.read_text("utf-8"))
        return crypto.decrypt(env), env.get("salt")
    except Exception as e:
        log(f"Edellistä dataa ei käytetty ({e.__class__.__name__}) – jatketaan ilman")
        return {}, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "symbols.enc"))
    ap.add_argument("--out", default=str(ROOT / "data" / "quotes.enc"))
    ap.add_argument("--previous", help="URL tai polku edelliseen quotes.enc-tiedostoon")
    args = ap.parse_args()

    password = os.environ.get("SALKKU_PASSWORD", "")
    if len(password) < 8:
        log("VIRHE: GitHubin salaisuus SALKKU_PASSWORD puuttuu tai on alle 8 merkkiä. "
            "Lisää se: Settings -> Secrets and variables -> Actions -> New repository secret.")
        sys.exit(1)
    crypto = Crypto(password)

    salt = None
    cfg_path = Path(args.config)
    if cfg_path.exists():
        env = json.loads(cfg_path.read_text("utf-8"))
        try:
            config = crypto.decrypt(env)
        except InvalidTag:
            log("VIRHE: symbols.enc ei aukea salaisuuden SALKKU_PASSWORD salasanalla. "
                "Salasana on vaihtunut tai kirjoitettu eri tavalla kuin sovelluksessa.")
            sys.exit(1)
        salt = env["salt"]
    else:
        log("symbols.enc puuttuu – julkaistaan tyhjä lista. Lisää kohteet sovelluksessa.")
        config = {"items": []}

    previous, prev_salt = load_previous(args.previous or args.out, crypto)
    salt = salt or prev_salt  # sama suola kaikkiin tiedostoihin
    prev_items = previous.get("items", {})
    cache = dict(previous.get("resolved", {}))
    items = [i for i in config.get("items", []) if item_id(i)]

    out = {"updated": None, "config": config, "items": {}, "errors": {}, "resolved": {}}
    bench_cache: dict = {}
    for item in items:
        iid = item_id(item)
        try:
            sym = resolve_symbol(item, cache)
            if item.get("isin"):
                out["resolved"][item["isin"].upper()] = sym
            prev = prev_items.get(iid)
            full = (not prev or prev.get("stale") or prev.get("symbol") != sym or "a" not in prev.get("daily", {})
                    or time.time() - int(prev.get("fullAt", 0)) > FULL_EVERY)
            with quiet():
                data = fetch(sym, bench_cache) if full else fetch_light(sym, prev, bench_cache)
        except Exception as e:
            out["errors"][iid] = f"{e.__class__.__name__}: {e}"[:300]
            if iid not in prev_items:
                continue
            data = {**prev_items[iid], "stale": True}
        try:
            data["sig"] = signals.evaluate(pd.Series(data["daily"].get("a") or data["daily"]["c"]),
                                           data.get("earn"), data.get("rel"))
        except Exception as e:
            log(f"Signaalin laskenta epäonnistui ({e.__class__.__name__})")
        out["items"][iid] = data
        time.sleep(0.4)

    ok = sum(1 for v in out["items"].values() if not v.get("stale"))
    out["health"] = {"ok": ok, "failed": len(out["errors"]), "total": len(items)}
    out["fx"] = fetch_fx({d.get("currency", "") for d in out["items"].values()}, previous.get("fx", {}))
    out["portfolio"] = portfolio(config, out["items"], out["fx"])
    out["notify"] = notify.run(config, out, previous.get("notify", {}), test=os.environ.get("TEST_PUSH") == "true")
    out["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(crypto.encrypt(out, salt)), "utf-8")
    log("Valmis: " + ("kaikki haut onnistuivat" if not out["errors"] else "osa hauista epäonnistui (katso sovelluksen Tila)"))


if __name__ == "__main__":
    main()

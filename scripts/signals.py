"""Ostotutkan signaalit.

Kaikki lasketaan osinko-oikaistuista päätöskursseista, jotta osingon irtoaminen ei näytä
kurssilaskulta. Pudotus suhteutetaan kohteen omaan heiluntaan: −20 % on tavallinen
heilahdus kasvuyhtiölle mutta poikkeuksellinen indeksirahastolle.

Pisteet:
  RSI(14) < 30 → +2, < 40 → +1
  pudotus 52 vk huipusta ≥ 1,5 × tavallinen 3 kk heilahdus → +2, ≥ 1,0 × → +1
  alle Bollingerin alanauhan (20 pv, 2σ) → +1
  notkahdus nousutrendissä (alle MA50, enintään 6 % yli nousevan MA200:n) → +1
  lähellä nousevaa MA200:aa (±2 %) → +1
  laskutrendi (alle laskevan MA200:n) → −1
≥ 2 = mahdollinen ostopaikka, ≥ 4 = vahva signaali, RSI > 70 = yliostettu.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

HORIZON = 63  # noin 3 kuukautta kaupankäyntipäivinä


def _fmt_pct(x: float) -> str:
    s = "+" if x > 0 else "−" if x < 0 else ""
    return f"{s}{abs(x) * 100:.1f} %".replace(".", ",")


def indicators(a: pd.Series) -> pd.DataFrame:
    """Tunnusluvut jokaiselle päivälle (a = osinko-oikaistu päätöskurssi)."""
    d = pd.DataFrame({"a": a.astype(float)})
    delta = d["a"].diff()
    g = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    l = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + g / l.replace(0, np.nan))
    d.loc[l == 0, "rsi"] = 100
    d["ma50"] = d["a"].rolling(50).mean()
    d["ma200"] = d["a"].rolling(200).mean()
    d["rising"] = d["ma200"] > d["ma200"].shift(20)
    d["falling"] = d["ma200"] < d["ma200"].shift(20)
    d["hi"] = d["a"].rolling(252, min_periods=60).max()
    d["lo"] = d["a"].rolling(252, min_periods=60).min()
    d["dd"] = d["a"] / d["hi"] - 1
    d["vol"] = d["a"].pct_change().rolling(252, min_periods=60).std()
    d["ddz"] = d["dd"] / (d["vol"] * math.sqrt(HORIZON))
    m20 = d["a"].rolling(20).mean()
    d["bbl"] = m20 - 2 * d["a"].rolling(20).std(ddof=0)

    rsi, a_, ma50, ma200 = d["rsi"], d["a"], d["ma50"], d["ma200"]
    dip = d["rising"] & (a_ > ma200) & (a_ < ma50) & (a_ / ma200 - 1 < 0.06)
    near = d["rising"] & ~dip & ((a_ / ma200 - 1).abs() < 0.02)
    down = (a_ < ma200) & d["falling"]
    d["score"] = (
        (rsi < 30) * 2 + ((rsi >= 30) & (rsi < 40)) * 1
        + (d["ddz"] <= -1.5) * 2 + ((d["ddz"] > -1.5) & (d["ddz"] <= -1.0)) * 1
        + (a_ < d["bbl"]) * 1 + dip * 1 + near * 1 - down * 1
    ).astype(int)
    d["dip"], d["near"], d["down"] = dip, near, down
    return d


def track_record(d: pd.DataFrame) -> dict | None:
    """Miten kurssi on kehittynyt 3 kk aiempien signaalien jälkeen (vähintään 20 pv väli)."""
    fwd = d["a"].shift(-HORIZON) / d["a"] - 1
    start = (d["score"] >= 2) & (d["score"].shift(1) < 2)
    events, last = [], -10**9
    for i, (ok, f) in enumerate(zip(start.values, fwd.values)):
        if ok and i - last >= 20 and not np.isnan(f):
            events.append(f)
            last = i
    base = fwd.dropna()
    if len(base) < 120:
        return None
    ev = np.array(events)
    return {
        "n": int(len(ev)),
        "med": round(float(np.median(ev)), 4) if len(ev) else None,
        "win": round(float((ev > 0).mean()), 3) if len(ev) else None,
        "bmed": round(float(base.median()), 4),
        "bwin": round(float((base > 0).mean()), 3),
    }


def evaluate(a: pd.Series, earn: str | None = None, rel: dict | None = None) -> dict | None:
    """Signaali viimeiselle päivälle. Palauttaa tunnusluvut ja perustelut suomeksi."""
    a = a.dropna()
    if len(a) < 30:
        return None
    d = indicators(a)
    r = d.iloc[-1]
    val = lambda x: None if pd.isna(x) else float(x)
    rsi, ddz, dd = val(r["rsi"]), val(r["ddz"]), val(r["dd"])
    reasons = []
    if rsi is not None and rsi < 30:
        reasons.append(f"RSI {rsi:.0f} – ylimyyty")
    elif rsi is not None and rsi < 40:
        reasons.append(f"RSI {rsi:.0f} – heikko, lähellä ylimyytyä")
    if ddz is not None and ddz <= -1.0:
        how = "poikkeuksellisen syvä" if ddz <= -1.5 else "tavallista syvempi"
        reasons.append(f"{_fmt_pct(dd)} 52 viikon huipusta – {how} pudotus tälle kohteelle")
    if not pd.isna(r["bbl"]) and r["a"] < r["bbl"]:
        reasons.append("Bollingerin alanauhan alla (20 pv)")
    if r["dip"]:
        reasons.append("Notkahdus nousutrendissä: alle MA50, lähellä nousevaa MA200:aa")
    elif r["near"]:
        reasons.append("Lähellä nousevaa 200 pv keskiarvoa (tukitaso)")
    if r["down"]:
        reasons.append("Huom: laskutrendi – alle laskevan MA200:n")
    if earn:
        try:
            days = (dt.date.fromisoformat(earn) - dt.date.today()).days
            if 0 <= days <= 14:
                e = dt.date.fromisoformat(earn)
                reasons.append(f"Huom: tulosjulkistus {e.day}.{e.month}. – kurssi voi liikkua voimakkaasti")
        except ValueError:
            pass
    if rel and rel.get("item") is not None and rel.get("bench") is not None:
        diff = rel["item"] - rel["bench"]
        if rel["item"] < -0.05 and diff <= -0.10:
            reasons.append(f"Lasku on yhtiökohtainen: 3 kk {_fmt_pct(rel['item'])}, {rel['name']} {_fmt_pct(rel['bench'])}")
        elif rel["item"] < -0.05 and abs(diff) < 0.05:
            reasons.append(f"Lasku seuraa koko markkinaa ({rel['name']} 3 kk {_fmt_pct(rel['bench'])})")

    score = int(r["score"])
    level = "strong" if score >= 4 else "buy" if score >= 2 else "hot" if (rsi or 0) > 70 else "none"
    return {
        "score": score, "level": level, "reasons": reasons,
        "rsi": None if rsi is None else round(rsi, 1),
        "ma50": None if pd.isna(r["ma50"]) else round(float(r["ma50"]), 4),
        "ma200": None if pd.isna(r["ma200"]) else round(float(r["ma200"]), 4),
        "rising": bool(r["rising"]),
        "hi": round(float(r["hi"]), 4) if not pd.isna(r["hi"]) else None,
        "lo": round(float(r["lo"]), 4) if not pd.isna(r["lo"]) else None,
        "dd": None if dd is None else round(dd, 4),
        "ddz": None if ddz is None else round(ddz, 2),
        "vol": None if pd.isna(r["vol"]) else round(float(r["vol"]) * math.sqrt(252), 3),
    }

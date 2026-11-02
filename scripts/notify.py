"""Push-ilmoitukset puhelimeen ja tietokoneelle (Web Push).

Sovellus luo VAPID-avainparin ja tallentaa sen sekä laitteiden tilaukset salattuun
seurantalistaan (symbols.enc), joten erillisiä GitHub-salaisuuksia ei tarvita.
Ilmoitusten sisältö salataan päästä päähän: välittävä palvelu (esim. Apple) ei näe sitä.

Ilmoitukset:
  aamukatsaus     arkipäivisin valittuna tuntina (oletus klo 9)
  ostosignaali    kun kohteen signaali nousee (ei mitään → mahdollinen → vahva)
  tavoitehinta    kun kurssi laskee omaan tavoitehintaan
  häiriö          kun kurssihaku epäonnistuu kokonaan (enintään 6 h välein)
  testi           kun ajo käynnistetään testi-ilmoitusvalinnalla
"""
from __future__ import annotations

import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

from common import log, quiet

HKI = ZoneInfo("Europe/Helsinki")
RANK = {"none": 0, "hot": 0, "buy": 1, "strong": 2}
LEVEL_FI = {"buy": "mahdollinen", "strong": "vahva"}
CUR = {"EUR": "€", "USD": "$", "DKK": "kr", "SEK": "kr", "NOK": "kr", "GBP": "£", "GBp": "p"}


def _pct(x: float) -> str:
    s = "+" if x > 0 else "−" if x < 0 else ""
    return f"{s}{abs(x) * 100:.1f} %".replace(".", ",")


def _num(v: float) -> str:
    return (f"{v:,.2f}" if abs(v) >= 10 else f"{v:.3f}").replace(",", " ").replace(".", ",")


def _sender(push: dict):
    """Palauttaa lähetysfunktion tai None, jos ilmoituksia ei ole otettu käyttöön."""
    vapid = (push or {}).get("vapid") or {}
    subs = [s for s in (push or {}).get("subs", []) if s.get("endpoint") and s.get("keys")]
    if not vapid.get("priv") or not subs:
        return None
    from py_vapid import Vapid
    from pywebpush import WebPushException, webpush

    key = Vapid.from_string(vapid["priv"])
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "salkku")
    claim_sub = f"https://{owner.lower()}.github.io"
    sent = {"n": 0}

    def send(title: str, body: str, tag: str, dead: set):
        payload = json.dumps({"title": title, "body": body, "tag": tag, "url": "./"}, ensure_ascii=False)
        for s in subs:
            if s["endpoint"] in dead:
                continue
            try:
                with quiet():
                    webpush({"endpoint": s["endpoint"], "keys": s["keys"]}, data=payload,
                            vapid_private_key=key, vapid_claims={"sub": claim_sub}, ttl=12 * 3600, timeout=15)
                sent["n"] += 1
            except WebPushException as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code in (404, 410):
                    dead.add(s["endpoint"])  # tilaus poistettu laitteelta
                log(f"Ilmoitusta ei voitu toimittaa yhdelle laitteelle (HTTP {code})")
            except Exception as e:
                log(f"Ilmoitusvirhe ({e.__class__.__name__})")
    send.sent = sent
    return send


def _rows(config: dict, out: dict):
    for it in config.get("items", []):
        iid = (it.get("symbol") or it.get("isin") or "").strip().upper()
        d = out["items"].get(iid)
        if d and not d.get("stale"):
            yield iid, it, d


def run(config: dict, out: dict, state: dict, test: bool = False) -> dict:
    push = config.get("push") or {}
    prefs = {"morning": True, "hour": 9, "signals": True, "targets": True, "failures": True, **(push.get("prefs") or {})}
    state = {"sig": {}, "tgt": {}, "dead": [], **(state or {})}
    send = _sender(push)
    if not send:
        return state
    dead = set(state.get("dead", []))
    now = dt.datetime.now(HKI)
    today = now.date().isoformat()
    name = lambda it, d: it.get("name") or d.get("yahooName") or d.get("symbol")

    if test:
        send("Salkku", "Testi-ilmoitus toimii ✓ Saat tästä lähtien valitsemasi ilmoitukset.", "test", dead)

    # Ostosignaalit (kootaan yhteen ilmoitukseen)
    new_sigs = []
    for iid, it, d in _rows(config, out):
        sig = d.get("sig") or {}
        lvl = sig.get("level", "none")
        prev = state["sig"].get(iid, {})
        r, prev_r = RANK[lvl], RANK.get(prev.get("lvl", "none"), 0)
        max_today = prev.get("max", 0) if prev.get("day") == today else 0
        if r > prev_r and r > max_today:  # signaali vahvistui eikä siitä ole tänään ilmoitettu
            new_sigs.append((r, f"{name(it, d)} ({LEVEL_FI[lvl]})", sig))
            state["sig"][iid] = {"lvl": lvl, "day": today, "max": r}
        elif r != prev_r:
            state["sig"][iid] = {**prev, "lvl": lvl}
    if new_sigs and prefs["signals"]:
        new_sigs.sort(key=lambda x: -x[0])
        if len(new_sigs) == 1:
            why = [r for r in new_sigs[0][2].get("reasons", []) if not r.startswith("Huom")][:2]
            body = new_sigs[0][1] + (": " + "; ".join(why) if why else "")
        else:
            body = ", ".join(n for _, n, _ in new_sigs)
        send("Uusi ostosignaali" if len(new_sigs) == 1 else f"{len(new_sigs)} uutta ostosignaalia", body, "signal", dead)

    # Tavoitehinnat
    for iid, it, d in _rows(config, out):
        target = it.get("target")
        if not target:
            state["tgt"].pop(iid, None)
            continue
        if d["price"] <= target and state["tgt"].get(iid) != target:
            if prefs["targets"]:
                send("Tavoitehinta saavutettu",
                     f"{name(it, d)} {_num(d['price'])} {CUR.get(d.get('currency'), d.get('currency', ''))} – tavoitteesi oli {_num(target)}.", f"t-{iid}", dead)
            state["tgt"][iid] = target
        elif d["price"] > target * 1.02:
            state["tgt"].pop(iid, None)

    # Aamukatsaus
    if prefs["morning"] and now.weekday() < 5 and now.hour >= int(prefs["hour"]) and state.get("morning") != today:
        rows = [(name(it, d), d["price"] / d["prevClose"] - 1, d) for _, it, d in _rows(config, out) if d.get("prevClose")]
        if rows:
            up = sum(1 for _, c, _ in rows if c > 0.00005)
            down = sum(1 for _, c, _ in rows if c < -0.00005)
            best, worst = max(rows, key=lambda r: r[1]), min(rows, key=lambda r: r[1])
            parts = [f"▲ {up} ▼ {down}. Paras {best[0]} {_pct(best[1])}, heikoin {worst[0]} {_pct(worst[1])}."]
            port = out.get("portfolio")
            if port and port.get("value"):
                parts.append(f"Salkku {_num(port['value'])} € ({_pct(port['day'])}).")
            radar = [f"{r[0]} ({LEVEL_FI[r[2]['sig']['level']]})" for r in rows
                     if (r[2].get("sig") or {}).get("level") in LEVEL_FI]
            parts.append("Ostotutka: " + (", ".join(radar) if radar else "ei signaaleja") + ".")
            send("Salkku – aamukatsaus", " ".join(parts), "morning", dead)
        state["morning"] = today

    # Häiriö: kaikki haut epäonnistuivat
    h = out.get("health") or {}
    if prefs["failures"] and h.get("total") and h.get("ok") == 0:
        last = state.get("fail", 0)
        if now.timestamp() - last > 6 * 3600:
            send("Salkku: kurssihaku ei toimi", "Kursseja ei saatu haettua. Avaa sovelluksen Tila-näkymä.", "fail", dead)
            state["fail"] = now.timestamp()

    state["dead"] = sorted(dead)
    if send.sent["n"]:
        log(f"Ilmoituksia lähetetty: {send.sent['n']}")
    return state

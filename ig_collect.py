#!/usr/bin/env python3
"""Coleta direta na API do Instagram (Instagram Login) e grava raw/ig_api.json.

Roda no GitHub Actions com o token em IG_TOKEN. Não imprime o token.
Complementa os dados do Supermetrics com o que só a API oficial entrega:
  - interações da conta por dia (curtidas, comentários, compartilhamentos,
    salvamentos, total), seguidores ganhos e perdidos por dia
  - público engajado (idade, gênero, cidade)
  - duração dos Reels (lida do arquivo de vídeo com ffprobe) e tempo médio
    assistido em milissegundos
Cada métrica é coletada de forma independente: se uma falhar, as demais seguem
e o erro fica registrado em "errors".
"""
import json, os, subprocess, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3))
API = "https://graph.instagram.com/v23.0"
TOKEN = os.environ.get("IG_TOKEN", "").strip()
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "raw", "ig_api.json")
DUR = os.path.join(HERE, "raw", "durations.json")
errors = []


def get(path, **params):
    params["access_token"] = TOKEN
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}" if not path.startswith("http") else path
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try: msg = json.loads(body).get("error", {}).get("message", body)
            except Exception: msg = body
            if e.code >= 500 and attempt < 2: time.sleep(3); continue
            raise RuntimeError(f"HTTP {e.code}: {msg[:300]}")
        except Exception as e:
            if attempt < 2: time.sleep(3); continue
            raise RuntimeError(str(e)[:300])


def safe(label, fn, default=None):
    try: return fn()
    except Exception as e:
        errors.append(f"{label}: {e}")
        return default


def day_bounds(d):
    start = datetime(d.year, d.month, d.day, tzinfo=BRT)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def total_value(res, metric):
    for m in res.get("data", []):
        if m.get("name") == metric:
            return m.get("total_value", {}).get("value")
    return None


QUICK = os.environ.get("IG_QUICK") == "1"  # botão "Atualizar agora": só o essencial, em segundos


def main():
    if not TOKEN:
        sys.exit("IG_TOKEN ausente")
    now = datetime.now(BRT)
    out = {"collected": now.replace(microsecond=0).isoformat(), "errors": errors}
    prev = {}
    if QUICK:
        try: prev = json.load(open(OUT, encoding="utf-8"))
        except Exception: prev = {}

    out["me"] = safe("me", lambda: get("me", fields="user_id,username,followers_count,follows_count,media_count"))

    # ---- interações e seguidores por dia (últimos 30 dias completos) ----
    METRICS = ["likes", "comments", "shares", "saves", "total_interactions", "reach", "views", "profile_links_taps"]
    daily = {}
    bad = set()
    for i in range(3 if QUICK else 30, 0, -1):
        d = (now - timedelta(days=i)).date()
        since, until = day_bounds(d)
        row = {}
        ok = [m for m in METRICS if m not in bad]
        res = safe(f"daily {d}", lambda: get("me/insights", metric=",".join(ok), period="day", metric_type="total_value", since=since, until=until))
        if res is None:  # tenta uma a uma para descobrir qual quebrou
            for m in ok:
                r = safe(f"daily {d} {m}", lambda: get("me/insights", metric=m, period="day", metric_type="total_value", since=since, until=until))
                if r is None: bad.add(m)
                else: row[m] = total_value(r, m)
        else:
            for m in ok: row[m] = total_value(res, m)
        fu = safe(f"follows {d}", lambda: get("me/insights", metric="follows_and_unfollows", period="day", metric_type="total_value", breakdown="follow_type", since=since, until=until))
        if fu:
            for m in fu.get("data", []):
                for b in m.get("total_value", {}).get("breakdowns", []):
                    for r in b.get("results", []):
                        k = (r.get("dimension_values") or [""])[0]
                        if k == "FOLLOWER": row["follows"] = r.get("value")
                        elif k == "NON_FOLLOWER": row["unfollows"] = r.get("value")
            row.setdefault("follows", 0 if fu.get("data") else None)
            row.setdefault("unfollows", 0 if fu.get("data") else None)
        daily[d.isoformat()] = row
    if QUICK: daily = {**(prev.get("daily") or {}), **daily}
    out["daily"] = daily

    # ---- público engajado ----
    eng = prev.get("engaged_audience", {}) if QUICK else {}
    for b in ([] if QUICK else ["age", "gender", "city"]):
        res = None
        for tf in ["last_30_days", "this_month"]:
            res = safe(f"engaged {b} {tf}", lambda: get("me/insights", metric="engaged_audience_demographics", period="lifetime", metric_type="total_value", breakdown=b, timeframe=tf))
            if res: break
        if res:
            rows = []
            for m in res.get("data", []):
                for bd in m.get("total_value", {}).get("breakdowns", []):
                    for r in bd.get("results", []):
                        rows.append([(r.get("dimension_values") or [""])[0], r.get("value")])
            eng[b] = rows
    out["engaged_audience"] = eng

    # ---- mídia: duração dos Reels e tempo médio assistido ----
    try: durations = json.load(open(DUR))
    except Exception: durations = {}
    cutoff = now - timedelta(days=7 if QUICK else 180)
    media, url = [], None
    params = dict(fields="id,shortcode,permalink,media_type,media_product_type,timestamp,media_url", limit=100)
    page = safe("media", lambda: get("me/media", **params))
    while page:
        stop = False
        for m in page.get("data", []):
            ts = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            if ts < cutoff: stop = True; break
            media.append(m)
        nxt = page.get("paging", {}).get("next")
        if stop or not nxt: break
        page = safe("media page", lambda: get(nxt))
    reels = {}
    probed = 0
    for m in media:
        if m.get("media_product_type") != "REELS" and m.get("media_type") != "VIDEO": continue
        code = m.get("shortcode") or m["permalink"].rstrip("/").split("/")[-1]
        rec = {}
        ins = safe(f"reel {code}", lambda: get(f"{m['id']}/insights", metric="ig_reels_avg_watch_time,views,reach"))
        if ins:
            for x in ins.get("data", []):
                v = (x.get("values") or [{}])[0].get("value")
                if v is None: v = x.get("total_value", {}).get("value")
                rec[x["name"]] = v
        if not QUICK and code not in durations and m.get("media_url") and probed < 400:
            try:
                p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", m["media_url"]],
                                   capture_output=True, text=True, timeout=60)
                durations[code] = round(float(p.stdout.strip()), 1)
            except Exception as e:
                errors.append(f"duração {code}: {str(e)[:120]}")
            probed += 1
        rec["duration_s"] = durations.get(code)
        reels[code] = rec
    # ---- stories ativos (a API só mostra as últimas 24 horas; o histórico fica em history.json) ----
    stories = {}
    page = safe("stories", lambda: get("me/stories", fields="id,media_type,timestamp,permalink"))
    SM = ["views", "reach", "replies", "shares", "total_interactions", "follows", "profile_visits"]
    for st in (page or {}).get("data", []):
        rec = {"ts": st.get("timestamp"), "type": st.get("media_type")}
        ins = safe(f"story {st['id']}", lambda: get(f"{st['id']}/insights", metric=",".join(SM)))
        if ins is None:
            for m in SM:
                r = safe(f"story {st['id']} {m}", lambda: get(f"{st['id']}/insights", metric=m))
                if r: ins = {"data": (ins or {}).get("data", []) + r.get("data", [])}
        for x in (ins or {}).get("data", []):
            v = (x.get("values") or [{}])[0].get("value")
            if v is None: v = x.get("total_value", {}).get("value")
            rec[x["name"]] = v
        stories[st["id"]] = rec
    out["stories"] = stories

    if QUICK:
        reels = {**(prev.get("reels") or {}), **reels}
        out["media_count_window"] = prev.get("media_count_window")
    else:
        out["media_count_window"] = len(media)
    out["reels"] = reels

    json.dump(durations, open(DUR, "w"), separators=(",", ":"))
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    filled = sum(1 for r in daily.values() if r.get("shares") is not None)
    print(("rápida: " if QUICK else "") + f"ok: {filled}/{len(daily)} dias com interações, {len(stories)} stories ativos, {len(reels)} Reels, {sum(1 for r in reels.values() if r.get('duration_s'))} com duração, {len(errors)} erro(s)")
    for e in errors[:15]: print(" -", e)


if __name__ == "__main__":
    main()

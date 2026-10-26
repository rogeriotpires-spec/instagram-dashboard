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
    METRICS = ["likes", "comments", "shares", "saves", "total_interactions", "reach", "views", "profile_links_taps",
               "accounts_engaged", "reposts", "replies"]
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
        if ins and not QUICK:  # extras da coleta completa (substituem o Supermetrics); cada um pode faltar sem quebrar
            for extra in ["ig_reels_video_view_total_time", "reels_skip_rate"]:
                r = safe(f"reel {code} {extra}", lambda: get(f"{m['id']}/insights", metric=extra))
                if r: ins["data"] = ins.get("data", []) + r.get("data", [])
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
    # ---- posts das últimas 48 horas com números atualizados (para o filtro de 1 dia mostrar o que saiu hoje) ----
    recent = {}
    lim = now - timedelta(hours=48)
    for m in media:
        ts = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        if ts < lim: continue
        code = m.get("shortcode") or m["permalink"].rstrip("/").split("/")[-1]
        is_reel = m.get("media_product_type") == "REELS" or m.get("media_type") == "VIDEO"
        mets = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions"] + ([] if is_reel else ["profile_visits", "follows"])
        rec = {"ts": m["timestamp"], "media_type": m.get("media_type"), "product": m.get("media_product_type"), "permalink": m.get("permalink")}
        info = safe(f"post {code}", lambda: get(m["id"], fields="caption,like_count,comments_count"))
        if info: rec["caption"] = info.get("caption"); rec["like_count"] = info.get("like_count"); rec["comments_count"] = info.get("comments_count")
        ins = safe(f"post {code} insights", lambda: get(f"{m['id']}/insights", metric=",".join(mets)))
        if ins is None:
            ins = {"data": []}
            for k in mets:
                r = safe(f"post {code} {k}", lambda: get(f"{m['id']}/insights", metric=k))
                if r: ins["data"] += r.get("data", [])
        for x in ins.get("data", []):
            v = (x.get("values") or [{}])[0].get("value")
            if v is None: v = x.get("total_value", {}).get("value")
            rec[x["name"]] = v
        recent[code] = rec
    out["recent_posts"] = recent

    # ---- stories ativos (a API só mostra as últimas 24 horas; o histórico fica em history.json) ----
    stories = {}
    page = safe("stories", lambda: get("me/stories", fields="id,media_type,timestamp,permalink", limit=100))
    active = []
    while page:
        active += page.get("data", [])
        nxt = page.get("paging", {}).get("next")
        if not nxt or len(active) > 300: break
        page = safe("stories page", lambda: get(nxt))
    SM = ["views", "reach", "replies", "shares", "total_interactions", "follows", "profile_visits"]
    for st in active:
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

    # endereço da imagem dos posts de imagem e carrossel dos últimos 14 dias (a análise semanal classifica o estilo visual)
    if QUICK:
        out["thumbs"] = prev.get("thumbs") or {}
    else:
        lim14 = now - timedelta(days=14)
        out["thumbs"] = {(m.get("shortcode") or m["permalink"].rstrip("/").split("/")[-1]): m.get("media_url")
                         for m in media if m.get("media_type") in ("IMAGE", "CAROUSEL_ALBUM") and m.get("media_url")
                         and datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z") >= lim14}

    if QUICK:
        reels = {**(prev.get("reels") or {}), **reels}
        out["media_count_window"] = prev.get("media_count_window")
    else:
        out["media_count_window"] = len(media)
    out["reels"] = reels

    if not QUICK:
        safe("arquivos brutos", lambda: write_raw(out, media, reels, now))

    json.dump(durations, open(DUR, "w"), separators=(",", ":"))
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    filled = sum(1 for r in daily.values() if r.get("shares") is not None)
    print(("rápida: " if QUICK else "") + f"ok: {filled}/{len(daily)} dias com interações, {len(stories)} stories ativos, {len(reels)} Reels, {sum(1 for r in reels.values() if r.get('duration_s'))} com duração, {len(errors)} erro(s)")
    for e in errors[:15]: print(" -", e)


# ---------- arquivos brutos no formato que o build.py lê (antes vinham do Supermetrics) ----------
RAW = os.path.join(HERE, "raw")


def _save(name, header, rows):
    if not rows:
        errors.append(f"raw/{name}.json: sem dados, mantido o arquivo anterior")
        return
    with open(os.path.join(RAW, name + ".json"), "w", encoding="utf-8") as f:
        json.dump([header] + rows, f, ensure_ascii=False, separators=(",", ":"))


def _old(name):
    try:
        d = json.load(open(os.path.join(RAW, name + ".json"), encoding="utf-8"))
        return d[0], d[1:]
    except Exception:
        return None, []


def _breakdown(res):
    rows = []
    for m in (res or {}).get("data", []):
        for bd in m.get("total_value", {}).get("breakdowns", []):
            for r in bd.get("results", []):
                rows.append((r.get("dimension_values") or [], r.get("value")))
    return rows


def _val(x):
    v = (x.get("values") or [{}])[0].get("value")
    return x.get("total_value", {}).get("value") if v is None else v


def write_raw(out, media, reels, now):
    daily = out.get("daily") or {}
    days = sorted(daily)

    # conta
    me = out.get("me") or {}
    if me.get("followers_count") is not None:
        _save("account", ["Username", "Profile followers", "Profile follows", "Profile media count"],
              [[me.get("username"), me.get("followers_count"), me.get("follows_count"), me.get("media_count")]])

    # métricas diárias da conta (90 dias: junta as novas com as antigas)
    h, old = _old("daily")
    by = {r[0]: r for r in old}
    for d in days:
        r = daily[d]
        if r.get("reach") is None and r.get("views") is None: continue
        by[d] = [d, r.get("reach"), r.get("views"), r.get("accounts_engaged"), r.get("reposts"), r.get("replies")]
    cut = (now - timedelta(days=90)).date().isoformat()
    _save("daily", ["Date", "Profile reach", "Profile views", "Accounts engaged", "Profile reposts", "Profile replies"],
          [by[d] for d in sorted(by) if d >= cut])

    # novos seguidores por dia e totais de 30 dias
    fol = [[d, daily[d].get("follows")] for d in days if daily[d].get("follows") is not None]
    _save("followers_daily", ["Date", "New followers"], fol)
    gained = sum(daily[d].get("follows") or 0 for d in days)
    lost = sum(daily[d].get("unfollows") or 0 for d in days)
    if fol: _save("follows_unfollows", ["Follow type", "Follows and unfollows"], [["FOLLOWER", gained], ["NON_FOLLOWER", lost]])

    # alcance e visualizações por tipo de público (seguidor ou não), 30 dias
    if days:
        since = day_bounds(datetime.fromisoformat(days[0]).date())[0]
        until = day_bounds(datetime.fromisoformat(days[-1]).date())[1]
        ft = {}
        for met in ["reach", "views"]:
            res = safe(f"{met} por tipo de público", lambda: get("me/insights", metric=met, period="day", metric_type="total_value", breakdown="follow_type", since=since, until=until))
            for dims, v in _breakdown(res):
                if dims: ft.setdefault(dims[0], {})[met] = v
        _save("follow_types", ["Follow type", "Profile reach", "Profile views"],
              [[k, v.get("reach"), v.get("views")] for k, v in ft.items() if k in ("FOLLOWER", "NON_FOLLOWER")])

    # demografia dos seguidores
    def demo(bd):
        for tf in [None, "this_month", "last_30_days", "last_90_days"]:
            kw = dict(metric="follower_demographics", period="lifetime", metric_type="total_value", breakdown=bd)
            if tf: kw["timeframe"] = tf
            res = safe(f"seguidores por {bd} ({tf or 'padrão'})", lambda: get("me/insights", **kw))
            rows = _breakdown(res)
            if rows: return rows
        return []
    G = {"M": "male", "F": "female", "U": "undefined"}
    _save("age_gender", ["Age", "Gender", "Profile followers"],
          [[d[0], G.get(d[1], d[1]), v] for d, v in demo("age,gender") if len(d) >= 2])
    _save("cities", ["Area & city", "Profile followers"],
          sorted(([d[0], v] for d, v in demo("city") if d), key=lambda r: -(r[1] or 0)))

    # posts dos últimos 180 dias com os números de cada um
    h, old = _old("media")
    bad = set()  # métricas que a API recusou: não pede de novo nos próximos posts
    by = {}
    for r in old:
        m = r[3] or ""
        by[m.rstrip("/").split("/")[-1]] = r
    for m in media:
        code = m.get("shortcode") or m["permalink"].rstrip("/").split("/")[-1]
        is_reel = m.get("media_product_type") == "REELS" or m.get("media_type") == "VIDEO"
        mets = [k for k in ["views", "reach", "total_interactions", "likes", "comments", "shares", "saved", "reposts"] + ([] if is_reel else ["profile_visits", "follows"]) if k not in bad]
        got = {}
        ins = safe(f"post {code} insights", lambda: get(f"{m['id']}/insights", metric=",".join(mets)))
        if ins is None:
            for k in mets:
                r = safe(f"post {code} {k}", lambda: get(f"{m['id']}/insights", metric=k))
                if r: got.update({x["name"]: _val(x) for x in r.get("data", [])})
                elif k == "reposts": bad.add(k)
        else:
            got = {x["name"]: _val(x) for x in ins.get("data", [])}
        if not got and code in by: continue  # falhou: mantém a leitura anterior
        info = safe(f"post {code} legenda", lambda: get(m["id"], fields="caption,like_count,comments_count")) or {}
        ts = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z").astimezone(BRT).strftime("%Y-%m-%d %H:%M:%S")
        g = lambda k, alt=None: got.get(k) if got.get(k) is not None else info.get(alt) if alt else None
        by[code] = [ts, m.get("media_type"), m.get("media_product_type"), m.get("permalink"), info.get("caption"),
                    g("views"), g("reach"), g("total_interactions"), g("likes", "like_count"), g("comments", "comments_count"),
                    g("shares"), g("saved"), g("reposts"), g("profile_visits"), g("follows")]
    cut = (now - timedelta(days=180)).strftime("%Y-%m-%d")
    _save("media", ["Media created", "Media type", "Media product type", "Media permalink", "Media caption", "Views", "Media reach",
                    "Interactions", "Like count", "Comments count", "Shares", "Unique saves", "Reposts", "Profile visits", "Follows"],
          sorted((r for r in by.values() if r[0] and r[0][:10] >= cut), key=lambda r: r[0], reverse=True))

    # Reels: tempo médio assistido, taxa de pulo e minutos vistos
    h, old = _old("reels")
    by = {r[0]: r for r in old}
    for code, r in reels.items():
        ms = r.get("ig_reels_avg_watch_time")
        if ms is None: continue
        sec = round(ms / 1000)
        tot = r.get("ig_reels_video_view_total_time")
        by[code] = [code, f"{sec // 60:02d}:{sec % 60:02d}", r.get("reels_skip_rate"), round(tot / 60000) if tot is not None else None]
    _save("reels", ["Media shortcode", "Reels average minutes watched", "Reels skip rate", "Reels minutes viewed"], list(by.values()))


if __name__ == "__main__":
    main()

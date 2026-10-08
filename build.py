#!/usr/bin/env python3
"""Monta data.json a partir das consultas brutas do Supermetrics em raw/.

Cada arquivo em raw/ é a lista "data" devolvida pelo Supermetrics: a primeira
linha é o cabeçalho e as demais são valores (sem compressão).

  raw/media.json            AccountMedia (180 dias)
  raw/reels.json            AccountMediaVideo (180 dias)
  raw/account.json          AccountCommon
  raw/daily.json            AccountInsightsDaily: date,reach,profile_views,accounts_engaged,profile_reposts,profile_replies
  raw/followers_daily.json  AccountInsightsDaily: date,follower_count (30 dias)
  raw/follows_unfollows.json AccountFollowTypes: follow_type,follows_and_unfollows (30 dias)
  raw/follow_types.json     AccountFollowTypes: follow_type,reach,profile_views (30 dias)
  raw/age_gender.json       FollowerDemographicsAgeGender
  raw/cities.json           FollowerDemographicsCity
  raw/stories.json          AccountMediaStory: timestamp,media_id,media_type,media_story_views,media_story_reach,media_story_replies,media_story_shares

Arquivos persistentes (não apagar):
  history.json  série diária da conta, total de seguidores por coleta e leituras
                de cada post por coleta (base das janelas 24h/72h/7d/30d)
  titles.json   título curto de cada post (código -> título)
  themes.json   tema de cada post (código -> tema); posts novos são
                classificados por palavras-chave da legenda

Uso: python3 build.py   (opcional: --collected 2026-10-07T04:50:00-03:00)
     python3 build.py --quick   atualização manual (botão do painel): mantém posts e
                                histórico da última coleta completa e só renova os
                                dados da conta a partir de raw/ig_api.json
"""
import json, re, sys, os
from datetime import datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3))
HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(HERE, *a)

def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except FileNotFoundError:
        if default is not None: return default
        raise

def rows(name):
    d = load(P("raw", name + ".json"))
    if isinstance(d, dict): d = d.get("data") or d.get("rows")
    return d[1:] if d and isinstance(d[0], list) and isinstance(d[0][0], str) and not re.match(r"\d{4}-", d[0][0]) else d

def num(v):
    if v in (None, "", "null"): return None
    try: return int(v)
    except (TypeError, ValueError):
        try: return float(v)
        except (TypeError, ValueError): return None

QUICK = "--quick" in sys.argv
collected = None
if QUICK:
    collected = datetime.fromisoformat(load(P("data.json"))["updated"])
if "--collected" in sys.argv: collected = datetime.fromisoformat(sys.argv[sys.argv.index("--collected") + 1])
collected = collected or datetime.now(BRT).replace(microsecond=0)
today = collected.date()
yesterday = today - timedelta(days=1)

hist = load(P("history.json"), {"daily": {}, "followers": {}, "posts": {}})
titles = load(P("titles.json"), {})
themes = load(P("themes.json"), {})
styles = load(P("styles.json"), {})  # estilo visual de cada post (realista, cartaz, foto-oficial, print, video), preenchido pela análise semanal

# ---------- tema por palavras-chave ----------
THEMES = ["Opinião", "Política", "Entrevista", "Bastidores e pessoal", "Serviço", "Eventos e discursos"]
RULES = [
    ("Entrevista", r"\bentrevist|\bexclusiva (com|para)|recado de .{0,40} para a revista|estreia como entrevistador"),
    ("Serviço", r"ordem de vota|colinha|gabarito eleitoral|como votar|passo a passo"),
    ("Eventos e discursos", r"^discurso|lançamento .{0,30}(campanha|flávio)|manifestação em|hino nacional|carta aos brasileiros|evento teste"),
    ("Bastidores e pessoal", r"bastidor|aniversário|heatwave|maratona|meu mico|mil seguidores|30\.000 pessoas|camisas da loja|tl tv no ar|rock in rio|vivendo nossa|anos juntos|gratidão a deus|resenha|clube da"),
]
def classify(caption, typ):
    c = re.sub(r"^[^\wÀ-ú]+", "", (caption or "").lower())[:160]
    for t, rx in RULES:
        if re.search(rx, c): return t
    return "Opinião" if typ in ("I", "C") else "Política"

def short_title(caption):
    c = re.sub(r"\s+", " ", (caption or "").replace("—", ",").replace("–", ",")).strip()
    c = re.sub(r"[#@]\S+", "", c).strip()
    first = re.split(r"(?<=[.!?…])\s", c)[0] if c else "Sem legenda"
    first = re.sub(r"^[^\wÀ-ú]+", "", first).strip(" \"“”")
    return (first[:57].rsplit(" ", 1)[0] + "…") if len(first) > 60 else first

# ---------- posts ----------
COLS = ["ts", "type", "code", "title", "theme", "views", "reach", "interactions", "likes", "comments", "shares", "saves", "reposts", "profile_visits", "follows", "age_h"]
posts = []
for r in rows("media"):
    ts, mtype, ptype, link, cap = r[0], r[1], r[2], r[3], r[4]
    m = re.search(r"/(?:p|reel|tv)/([^/]+)/", link or "")
    if not m: continue
    code = m.group(1)
    typ = "C" if mtype == "CAROUSEL_ALBUM" else ("R" if mtype == "VIDEO" or ptype == "REELS" else "I")
    if code not in titles: titles[code] = short_title(cap)
    if code not in themes: themes[code] = classify(cap, typ)
    t = datetime.fromisoformat(ts.replace(" ", "T") + ("" if len(ts) > 16 else ":00")).replace(tzinfo=BRT)
    age_h = round((collected - t).total_seconds() / 3600, 1)
    vals = [num(x) for x in r[5:15]]
    posts.append([ts[:16], typ, code, titles[code], themes[code], *vals, age_h])
    # leitura desta coleta
    snap = hist["posts"].setdefault(code, [])
    rec = [collected.isoformat(), age_h, vals[0], vals[1], vals[3], vals[4], vals[5], vals[6], vals[9]]
    if not snap or snap[-1][0][:10] != rec[0][:10]: snap.append(rec)
    else: snap[-1] = rec
posts.sort(key=lambda p: p[0], reverse=True)

reels = {}
for r in rows("reels"):
    code, w, s, mins = r[0], r[1], num(r[2]), num(r[3])
    if not code or w in (None, "", "null") or s is None: continue
    mm, ss = str(w).split(":")[-2:]
    reels[code] = [int(mm) * 60 + int(ss), s, mins, None, None]

# ---------- API direta do Instagram (GitHub Actions grava raw/ig_api.json) ----------
try: ig = load(P("raw", "ig_api.json"))
except Exception: ig = {}
for code, r in (ig.get("reels") or {}).items():
    dur, ms = r.get("duration_s"), r.get("ig_reels_avg_watch_time")
    if code not in reels:
        if ms is None: continue
        reels[code] = [round(ms / 1000), None, None, None, None]
    reels[code][3] = dur
    if dur and ms is not None: reels[code][4] = round(min(ms / 1000 / dur, 1.5), 4)
api_days = sorted((ig.get("daily") or {}))
hist.setdefault("daily_api", {})
for d in api_days:
    r = dict(ig["daily"][d])
    if d == api_days[-1] and not r.get("follows") and not r.get("unfollows"):
        r["follows"] = r["unfollows"] = None  # dia ainda não processado pela fonte
    hist["daily_api"][d] = r

# stories: a API só mostra os ativos (24h); guardamos a última leitura de cada um
hist.setdefault("stories", {})
for sid, r in (ig.get("stories") or {}).items():
    if not r.get("ts"): continue
    cur = hist["stories"].get(sid, {})
    rec = {"ts": r["ts"], "type": r.get("type")}
    for k in ["views", "reach", "replies", "shares", "total_interactions", "follows", "profile_visits"]:
        v, o = r.get(k), cur.get(k)
        rec[k] = max(v, o) if v is not None and o is not None else (v if v is not None else o)
    rec["seen"] = ig.get("collected")
    hist["stories"][sid] = rec
# stories pelo Supermetrics (enxerga todos, inclusive os que a API do Instagram omite)
try: sm_st = rows("stories")
except Exception: sm_st = []
for r in sm_st:
    ts, sid = r[0], str(r[1])
    if not ts or not sid: continue
    cur = hist["stories"].get(sid, {})
    rec = {"ts": cur.get("ts") or ts, "type": r[2] or cur.get("type")}
    for k, v in zip(["views", "reach", "replies", "shares"], [num(x) for x in r[3:7]]):
        o = cur.get(k)
        rec[k] = max(v, o) if v is not None and o is not None else (v if v is not None else o)
    for k in ["total_interactions", "follows", "profile_visits"]: rec[k] = cur.get(k)
    rec["seen"] = max(cur.get("seen") or "", collected.isoformat())
    hist["stories"][sid] = rec
def _bt(ts):  # timestamp da API (UTC, +0000) para horário de Brasília
    t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z") if "T" in ts else datetime.fromisoformat(ts).replace(tzinfo=BRT)
    return t.astimezone(BRT)
stories = sorted(([_bt(r["ts"]).strftime("%Y-%m-%d %H:%M"), r.get("type"), r.get("views"), r.get("reach"), r.get("replies"), r.get("shares"), r.get("seen")]
                  for r in hist["stories"].values()), reverse=True)
stories = [x for x in stories if x[0][:10] >= (today - timedelta(days=90)).isoformat()]

# ---------- conta ----------
acc = rows("account")[0]
account = {"username": acc[0], "followers": num(acc[1]), "follows": num(acc[2]), "media_count": num(acc[3])}
account_updated = collected.isoformat()
if QUICK:
    me = ig.get("me") or {}
    if me.get("followers_count") is None: sys.exit("erro: raw/ig_api.json sem seguidores; nada atualizado")
    account.update({"followers": me["followers_count"], "follows": me.get("follows_count"), "media_count": me.get("media_count")})
    account_updated = ig.get("collected") or datetime.now(BRT).replace(microsecond=0).isoformat()
else:
    hist["followers"][collected.isoformat()] = account["followers"]

for r in rows("daily"):
    d = r[0][:10]
    cur = hist["daily"].get(d, [None] * 6)
    cur[:5] = [num(x) for x in r[1:6]]
    hist["daily"][d] = cur
fd = rows("followers_daily")
last_fd = max((r[0][:10] for r in fd), default=None)
for r in fd:
    d, v = r[0][:10], num(r[1])
    if d == last_fd and v == 0: v = None  # dia mais recente ainda não processado pela fonte
    cur = hist["daily"].get(d, [None] * 6)
    cur[5] = v
    hist["daily"][d] = cur

# total de seguidores por dia, a partir das coletas (uma por dia, por volta das 5h)
fol_by_day = {}
for iso, v in sorted(hist["followers"].items()):
    fol_by_day[iso[:10]] = v
days = sorted(hist["daily"])[-180:]
daily = []
for d in days:
    reach, views, eng, rep, repl, newf = hist["daily"][d]
    _api = hist.get("daily_api", {}).get(d, {})
    if newf is None and _api.get("follows") is not None:
        newf = _api["follows"]  # a API do Instagram processa o dia antes do Supermetrics
    # total ao fim do dia d ≈ coleta da manhã de d+1
    nxt = (datetime.fromisoformat(d) + timedelta(days=1)).date().isoformat()
    total = fol_by_day.get(nxt)
    prev_total = fol_by_day.get(d)
    net = total - prev_total if total is not None and prev_total is not None else None
    lost = newf - net if net is not None and newf is not None else None
    api = hist.get("daily_api", {}).get(d, {})
    if api.get("unfollows") is not None:
        lost = api["unfollows"]
        g = newf if newf is not None else api.get("follows")
        net = g - lost if g is not None else net
    daily.append([d, reach, views, eng, rep, repl, newf, total, net, lost,
                  api.get("likes"), api.get("comments"), api.get("shares"), api.get("saves"), api.get("total_interactions"), api.get("follows")])

fu = {r[0]: num(r[1]) for r in rows("follows_unfollows")}
ft = {r[0]: {"reach": num(r[1]), "views": num(r[2])} for r in rows("follow_types")}
CITY_UF = {"Rio de Janeiro (state)": "RJ", "São Paulo (state)": "SP", "Minas Gerais": "MG", "Ceará": "CE", "Bahia": "BA", "Paraná": "PR", "Rio Grande do Sul": "RS", "Pernambuco": "PE", "Amazonas": "AM", "Goiás": "GO", "Distrito Federal": "DF", "Federal District": "DF", "Santa Catarina": "SC", "Pará": "PA", "Espírito Santo": "ES", "Paraíba": "PB", "Rio Grande do Norte": "RN", "Alagoas": "AL", "Mato Grosso": "MT", "Mato Grosso do Sul": "MS", "Maranhão": "MA", "Piauí": "PI", "Sergipe": "SE", "Tocantins": "TO", "Rondônia": "RO", "Acre": "AC", "Amapá": "AP", "Roraima": "RR"}
def city(n):
    if ", " not in n: return n
    c, st = n.split(", ", 1)
    return f"{c} ({CITY_UF.get(st, st)})"
REGIAO = {"SP": "Sudeste", "RJ": "Sudeste", "MG": "Sudeste", "ES": "Sudeste", "PR": "Sul", "SC": "Sul", "RS": "Sul",
          "BA": "Nordeste", "PE": "Nordeste", "CE": "Nordeste", "MA": "Nordeste", "PB": "Nordeste", "RN": "Nordeste", "AL": "Nordeste", "PI": "Nordeste", "SE": "Nordeste",
          "AM": "Norte", "PA": "Norte", "TO": "Norte", "RO": "Norte", "AC": "Norte", "AP": "Norte", "RR": "Norte",
          "GO": "Centro-Oeste", "DF": "Centro-Oeste", "MT": "Centro-Oeste", "MS": "Centro-Oeste"}
def states(rs):
    agg = {}
    for r in rs:
        if ", " not in r[0]: continue
        uf = CITY_UF.get(r[0].split(", ", 1)[1])
        if not uf: continue
        a = agg.setdefault(uf, [uf, REGIAO.get(uf, ""), 0, 0])
        a[2] += num(r[1]); a[3] += 1
    return sorted(agg.values(), key=lambda x: -x[2])

# leituras por idade do post: valor mais próximo de cada janela, sem extrapolar
WINDOWS = [24, 72, 168, 720]
post_windows = {}
for code, snaps in hist["posts"].items():
    out = {}
    for w in WINDOWS:
        cand = [s for s in snaps if s[1] is not None and abs(s[1] - w) <= max(12, w * 0.25)]
        if cand:
            s = min(cand, key=lambda s: abs(s[1] - w))
            out[str(w)] = {"age_h": s[1], "views": s[2], "reach": s[3], "comments": s[4], "shares": s[5], "saves": s[6], "follows": s[8]}
    if out: post_windows[code] = out

data = {
    "updated": collected.isoformat(),
    "account": account,
    "account_updated": account_updated,
    "period": {"start": min((p[0][:10] for p in posts), default=None), "end": yesterday.isoformat()},
    "columns": COLS, "posts": posts,
    "themes": THEMES,
    "reels_columns": ["avg_watch_s", "skip_rate", "minutes_viewed", "duration_s", "pct_watched"], "reels": reels,
    "daily_columns": ["date", "reach", "views", "accounts_engaged", "reposts", "replies", "new_followers", "followers_total", "net", "lost", "likes", "comments", "shares", "saves", "interactions", "gained_api"],
    "api_collected": ig.get("collected"),
    "stories_columns": ["ts", "type", "views", "reach", "replies", "shares", "seen"],
    "stories": stories,
    "daily": daily,
    "follows_30d": {"gained": fu.get("FOLLOWER"), "lost": fu.get("NON_FOLLOWER"), "end": yesterday.isoformat(), "start": (yesterday - timedelta(days=29)).isoformat()},
    "reach_by_follow_type_30d": {"non_follower": ft.get("NON_FOLLOWER"), "follower": ft.get("FOLLOWER")},
    "audience": {"ref_date": yesterday.isoformat(),
                 "age_gender": [[r[0], r[1], num(r[2])] for r in rows("age_gender")],
                 "cities": [[city(r[0]), num(r[1])] for r in rows("cities")],
                 "states_columns": ["uf", "region", "followers", "cities"],
                 "states": states(rows("cities"))},
    "post_windows": post_windows,
    "styles": styles,
    "tracking_since": min(hist["followers"]) if hist["followers"] else None,
}

for name, obj in [("data.json", data), ("history.json", hist), ("titles.json", titles), ("themes.json", themes)]:
    with open(P(name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
print(f"ok: {len(posts)} posts, {len(reels)} reels, {len(daily)} dias, {len(post_windows)} posts com janelas")

"""Coleta as manchetes recentes dos portais de noticias/config.json.

Portais com "feed" ou "feeds" são lidos no RSS/Atom. O Google Notícias entra
só como reserva, quando o feed falta, falha ou não traz nada na janela.
Em HTTP 429 a reserva espera com backoff exponencial e jitter.
Nomes de veículo (busca por tema e fonte do Google) são normalizados pelo
mapa de apelidos do config; o que não é portal da lista é descartado.
A janela continua sendo a das últimas N horas (padrão 12).

Uso:
  python3 tools/coleta_noticias.py [horas] > coleta-noticias.json
  python3 tools/coleta_noticias.py --self-test

O JSON da coleta vai para a saída padrão. O quadro por fonte vai para o
erro padrão e para coleta-diagnostico.json.
"""
import html
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feeds_diretos

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
UA = {"User-Agent": "Mozilla/5.0 (painel-rogerrio; +https://rogeriotpires-spec.github.io/instagram-dashboard/)"}
DIAG = os.path.join(ROOT, "coleta-diagnostico.json")

TEMAS = {
    "educacao": ["MEC OR Enem OR Fies OR Prouni OR Inep", "universidade federal OR instituto federal OR reitoria", "escola pública OR professores OR rede estadual de ensino", "UERJ OR UENF OR FAETEC OR CEDERJ OR CECIERJ OR \"Seeduc\""],
    "rio": ["\"governo do Rio\" OR \"Cláudio Castro\" OR Alerj", "\"Prefeitura do Rio\" OR \"Câmara do Rio\" OR \"Eduardo Paes\"", "\"Rio de Janeiro\" polícia OR operação OR segurança", "Niterói OR \"Baixada Fluminense\" OR \"São Gonçalo\""],
    "ciencia": ["inteligência artificial", "ciência OR pesquisa científica OR CNPq OR Capes OR Finep OR FAPERJ", "startup OR inovação OR tecnologia big tech", "saúde OR vacina OR medicina estudo"],
    "esportes": ["Flamengo OR Fluminense OR Vasco OR Botafogo", "seleção brasileira OR CBF OR Brasileirão", "esporte olímpico OR vôlei OR Fórmula 1 OR tênis"],
}


def norm_nome(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("+", " mais ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def construir_mapa(cfg):
    mapa = {}

    def add(alias, nome):
        k = norm_nome(alias)
        if k and k not in mapa:
            mapa[k] = nome

    for o in cfg["outlets"]:
        add(o["name"], o["name"])
    for alias, nome in (cfg.get("_aliases") or {}).items():
        if any(o["name"] == nome for o in cfg["outlets"]):
            add(alias, nome)
    drops = {norm_nome(x) for x in (cfg.get("_nao_portais") or []) if norm_nome(x)}
    return mapa, drops


def canonizar(fonte, mapa, drops):
    if not fonte:
        return None
    k = norm_nome(fonte)
    if not k or k in drops:
        return None
    if k in mapa:
        return mapa[k]
    k2 = re.sub(r"\b(noticias|news|jornal|portal|online|com|br)\b", " ", k)
    k2 = re.sub(r"\s+", " ", k2).strip()
    if k2 in drops:
        return None
    if k2 in mapa:
        return mapa[k2]
    melhor = None
    for alias, nome in mapa.items():
        if len(alias) < 6:
            continue
        if re.search(rf"(?:^| ){re.escape(alias)}(?: |$)", k) or (k2 and re.search(rf"(?:^| ){re.escape(alias)}(?: |$)", k2)):
            if melhor is None or len(alias) > len(melhor[0]):
                melhor = (alias, nome)
    return melhor[1] if melhor else None


def dominio(u):
    p = urllib.parse.urlparse(u)
    h = p.netloc.replace("www.", "")
    path = p.path.strip("/")
    if path and h in ("bbc.com", "dw.com", "msn.com", "otempo.com.br"):
        return h + "/" + path.split("/")[0]
    return h


def feeds_de(o):
    urls = []
    if isinstance(o.get("feeds"), list):
        urls.extend(x for x in o["feeds"] if x)
    if o.get("feed") and o["feed"] not in urls:
        urls.insert(0, o["feed"])
    # preserva ordem e tira repetido
    vistos, saida = set(), []
    for u in urls:
        if u not in vistos:
            vistos.add(u)
            saida.append(u)
    return saida


def _google_url(q):
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": q, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419",
    })


def baixar_google(url, ritmo):
    """Baixa um RSS do Google. Em 429/5xx, espera com backoff curto e jitter.
    Três 429 seguidos desligam a reserva nesta rodada: insistir não traz manchete."""
    if ritmo.get("google_off"):
        return None, b"", "reserva Google desligada após 429"
    ultimo = "sem resposta"
    status = None
    for i in range(3):
        time.sleep(ritmo["pausa"] + random.uniform(0.4, 1.1))
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=18) as r:
                ritmo["pausa"] = max(2.4, ritmo["pausa"] * 0.95)
                ritmo["seguidos_429"] = 0
                return r.status, r.read(), None
        except urllib.error.HTTPError as e:
            status = e.code
            ultimo = f"HTTP {e.code}"
            if e.code in (429, 500, 502, 503, 504):
                ritmo["http_429"] = ritmo.get("http_429", 0) + (1 if e.code == 429 else 0)
                if e.code == 429:
                    ritmo["seguidos_429"] = ritmo.get("seguidos_429", 0) + 1
                    if ritmo["seguidos_429"] >= 3:
                        ritmo["google_off"] = True
                        return status, b"", "Google Notícias em 429 repetido; reserva desligada nesta rodada"
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra and str(ra).isdigit():
                    espera = min(30, int(ra))
                else:
                    espera = min(24, 2.2 * (2 ** i)) + random.uniform(0.4, 1.4)
                ritmo["pausa"] = min(6.0, ritmo["pausa"] * 1.5)
                time.sleep(espera)
                continue
            break
        except Exception as e:
            ultimo = f"{type(e).__name__}: {str(e)[:100]}"
            time.sleep(min(6, 1.1 * (2 ** i)) + random.uniform(0, 0.3))
    return status, b"", ultimo


def itens_google(dados, corte, BRT, agora, limite, mapa, drops, descartados):
    try:
        raiz = ET.fromstring(dados)
    except ET.ParseError:
        return [], "XML inválido"
    itens = []
    for it in raiz.iter("item"):
        t = (it.findtext("title") or "").strip()
        fonte = (it.findtext("source") or "").strip()
        if fonte and t.endswith(" - " + fonte):
            t = t[: -len(" - " + fonte)].strip()
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(BRT)
        except Exception:
            dt = None
        if not t or not dt or dt < corte or dt > agora + timedelta(minutes=30):
            continue
        nome = canonizar(fonte, mapa, drops) if mapa is not None else fonte
        if mapa is not None:
            if not nome:
                if fonte:
                    descartados[fonte.strip() or "(sem fonte)"] += 1
                continue
        else:
            nome = None
        desc = re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or ""))
        desc = re.sub(r"\s+", " ", desc).strip()
        if desc.lower() == t.lower() or len(desc) < 40:
            desc = ""
        item = {"titulo": t, "descricao": desc[:500], "quando": dt.strftime("%d/%m %H:%M"), "link": it.findtext("link") or "", "_epoch": dt.timestamp()}
        if nome:
            item["fonte"] = nome
            item["fonte_bruta"] = fonte
        itens.append(item)
        if len(itens) >= limite:
            break
    return itens, None


def _portal_vazio(o, erros, http, feed_usado, via="nenhuma"):
    return {
        "nome": o["name"], "camp": o.get("camp"), "tier": o.get("tier"),
        "dominio": dominio(o["url"]), "via": via, "manchetes": [],
        "_diag": {"nome": o["name"], "via": via, "status": "fail", "count": 0,
                  "erro": "; ".join(erros)[:240] or None, "http": http, "feed": feed_usado},
    }


def deve_repetir_feed(st, tentativa, max_tent=3):
    """Repete em 429/5xx, timeout ou falha de rede. Janela vazia com HTTP 200 não ajuda insistir."""
    if tentativa >= max_tent - 1:
        return False
    if st.get("ok") and st.get("itens"):
        return False
    http = st.get("http")
    if http in (429, 500, 502, 503, 504) or http is None:
        return True
    erro = (st.get("erro") or "").lower()
    return any(x in erro for x in ("timeout", "temporar", "reset", "urlerror", "falha de rede", "429", "503"))


def ler_feeds(o, corte, BRT, agora, pausa=None):
    """Só os RSS do config. Não chama o Google (a reserva é sequencial, para não levar 429)."""
    itens, erros, http, feed_usado = [], [], None, None
    sleep = pausa or time.sleep
    try:
        for url in feeds_de(o):
            st = {}
            for tentativa in range(3):
                st = feeds_diretos.ler_status(url, corte, BRT, agora, limite=40)
                if st.get("ok") and st.get("itens"):
                    break
                if not deve_repetir_feed(st, tentativa):
                    break
                sleep((2 ** tentativa) * 0.7 + random.random() * 0.4)
            http = st.get("http")
            feed_usado = url
            if st.get("ok") and st.get("itens"):
                itens = st["itens"]
                break
            if st.get("erro"):
                erros.append(f"{st['erro']}")
            elif st.get("vazio"):
                erros.append("feed sem item na janela")
    except Exception as e:
        erros.append(f"{type(e).__name__}: {str(e)[:80]}")
    for m in itens:
        m.pop("_epoch", None)
    if itens:
        return {
            "nome": o["name"], "camp": o.get("camp"), "tier": o.get("tier"),
            "dominio": dominio(o["url"]), "via": "feed", "manchetes": itens,
            "_diag": {"nome": o["name"], "via": "feed", "status": "ok", "count": len(itens),
                      "erro": None, "http": http, "feed": feed_usado},
        }
    return _portal_vazio(o, erros, http, feed_usado)


def reserva_google(portal, o, corte, BRT, agora, ritmo):
    if portal.get("manchetes"):
        return portal
    erros = [portal["_diag"]["erro"]] if portal["_diag"].get("erro") else []
    try:
        q = f"site:{dominio(o['url'])} when:{ritmo['horas']}h"
        status, dados, erro = baixar_google(_google_url(q), ritmo)
        if dados:
            brutos, erro_xml = itens_google(dados, corte, BRT, agora, 25, None, None, None)
            if erro_xml:
                erros.append(erro_xml)
            else:
                for m in brutos:
                    m.pop("_epoch", None)
                    m.pop("fonte", None)
                    m.pop("fonte_bruta", None)
                portal["manchetes"] = brutos
                portal["via"] = "google"
                portal["_diag"].update(via="google", status="ok" if brutos else "fail", count=len(brutos), http=status, erro=None if brutos else "; ".join(erros)[:240] or "google sem item na janela")
                return portal
        elif erro:
            erros.append("google: " + erro)
        portal["_diag"].update(via="google", status="fail", http=status, erro="; ".join(x for x in erros if x)[:240])
    except Exception as e:
        portal["_diag"].update(via="google", status="fail", erro=f"{type(e).__name__}: {str(e)[:80]}")
    return portal


def temas(corte, BRT, agora, horas, ritmo, mapa, drops, descartados):
    saida = {}
    diag = {}
    for tema, qs in TEMAS.items():
        vistos, lista, erros = set(), [], []
        for q in qs:
            status, dados, erro = baixar_google(_google_url(q + f" when:{horas}h"), ritmo)
            if erro and not dados:
                erros.append(erro)
                continue
            if not dados:
                continue
            brutos, erro_xml = itens_google(dados, corte, BRT, agora, 40, mapa, drops, descartados)
            if erro_xml:
                erros.append(erro_xml)
                continue
            for m in brutos:
                chave = (m.get("fonte"), norm_nome(m["titulo"])[:80])
                if chave in vistos:
                    continue
                vistos.add(chave)
                m.pop("_epoch", None)
                m.pop("fonte_bruta", None)
                lista.append(m)
                if len(lista) >= 60:
                    break
            if len(lista) >= 60:
                break
        saida[tema] = lista
        diag[tema] = {"status": "ok" if lista else "fail", "count": len(lista), "erros": erros[:4]}
    return saida, diag


def quadro(diag):
    linhas = ["FONTE                         VIA      STATUS    N  DETALHE"]
    for f in diag["fontes"]:
        detalhe = f.get("erro") or (f.get("feed") or "")
        linhas.append(f"{f['nome'][:28]:28} {f['via'][:8]:8} {f['status']:8} {f['count']:3}  {str(detalhe)[:80]}")
    for tema, info in diag.get("temas", {}).items():
        linhas.append(f"tema {tema:22} {'google':8} {info['status']:8} {info['count']:3}")
    if diag.get("fontes_descartadas"):
        top = ", ".join(f"{n} {c}" for n, c in diag["fontes_descartadas"][:8])
        linhas.append("descartadas (não estão na lista): " + top)
    linhas.append(diag.get("resumo", ""))
    return "\n".join(linhas)


def coletar(horas):
    os.chdir(ROOT)
    cfg = json.load(open("noticias/config.json", encoding="utf-8"))
    mapa, drops = construir_mapa(cfg)
    BRT = timezone(timedelta(hours=-3))
    agora = datetime.now(BRT)
    corte = agora - timedelta(hours=horas)
    ritmo = {"pausa": 1.6, "horas": horas, "http_429": 0}
    descartados = Counter()
    portais = []
    # feeds em paralelo; o Google da reserva fica sequencial dentro de cada portal que precisar
    from concurrent.futures import ThreadPoolExecutor
    alvos = [o for o in cfg["outlets"] if o.get("fetch") is not False]
    with ThreadPoolExecutor(max_workers=12) as ex:
        portais = list(ex.map(lambda o: ler_feeds(o, corte, BRT, agora), alvos))
    print(f"feeds: {sum(1 for p in portais if p['manchetes'])}/{len(portais)} com manchete", file=sys.stderr, flush=True)
    # Temas primeiro, para o Google 429 não queimar a reserva dos portais que ainda faltam.
    blocos_tema, diag_temas = temas(corte, BRT, agora, horas, ritmo, mapa, drops, descartados)
    # Fallback por fonte, inclusive regional com feed vazio ou sem feed.
    ritmo["pausa"] = max(float(ritmo.get("pausa") or 1.6), 2.0)
    for o, portal in zip(alvos, portais):
        if portal["manchetes"]:
            continue
        if ritmo.get("google_off"):
            prev = portal["_diag"].get("erro") or "sem manchete no feed"
            portal["_diag"].update(
                via=portal["_diag"].get("via") or "nenhuma",
                status="fail",
                erro=f"{prev}; reserva Google não tentada (429 repetido)",
            )
            continue
        reserva_google(portal, o, corte, BRT, agora, ritmo)
        print(f"google {o['name']}: {portal['_diag']['status']} {portal['_diag']['count']} {portal['_diag'].get('erro') or ''}", file=sys.stderr, flush=True)
    ordem = {o["name"]: i for i, o in enumerate(alvos)}
    portais.sort(key=lambda p: ordem.get(p["nome"], 999))
    for p in portais:
        diag = p.pop("_diag")
        p["_diag"] = diag
    fontes = [p.pop("_diag") for p in portais]
    tot = sum(len(p["manchetes"]) for p in portais)
    com = sum(1 for p in portais if p["manchetes"])
    resumo = (
        f"{tot} manchetes de {com} de {len(portais)} portais nas últimas {horas} horas; "
        + "temas: " + ", ".join(f"{k} {len(v)}" for k, v in blocos_tema.items())
        + f"; Google 429: {ritmo['http_429']}"
    )
    diag = {
        "coletado": agora.replace(microsecond=0).isoformat(),
        "horas": horas,
        "fontes": fontes,
        "temas": diag_temas,
        "google_429": ritmo["http_429"],
        "fontes_descartadas": descartados.most_common(20),
        "resumo": resumo,
    }
    saida = {
        "coletado": diag["coletado"],
        "horas": horas,
        "portais": portais,
        "temas": blocos_tema,
        "resumo": resumo,
        "fontes": [{"nome": f["nome"], "via": f["via"], "status": f["status"], "count": f["count"]} for f in fontes],
    }
    with open(DIAG, "w", encoding="utf-8") as fh:
        json.dump(diag, fh, ensure_ascii=False, indent=1)
    print(quadro(diag), file=sys.stderr)
    return saida


def _self_test():
    cfg = {
        "outlets": [
            {"name": "O Globo", "url": "https://oglobo.globo.com"},
            {"name": "UOL", "url": "https://noticias.uol.com.br"},
            {"name": "BBC News Brasil", "url": "https://www.bbc.com/portuguese"},
            {"name": "G1", "url": "https://g1.globo.com", "fetch": False},
        ],
        "_aliases": {"O GLOBO": "O Globo", "BBC": "BBC News Brasil", "UOL Notícias": "UOL", "G1": "G1"},
        "_nao_portais": ["Instagram", "Facebook", "YouTube", "TikTok"],
    }
    mapa, drops = construir_mapa(cfg)
    assert canonizar("O GLOBO", mapa, drops) == "O Globo"
    assert canonizar("UOL Notícias", mapa, drops) == "UOL"
    assert canonizar("BBC News", mapa, drops) == "BBC News Brasil"
    assert canonizar("Instagram", mapa, drops) is None
    assert canonizar("Facebook Watch", mapa, drops) is None
    assert canonizar("Veículo Inventado", mapa, drops) is None
    assert deve_repetir_feed({"http": 429, "erro": "429"}, 0)
    assert deve_repetir_feed({"http": 503, "erro": "temporar"}, 1)
    assert not deve_repetir_feed({"http": 200, "vazio": True, "ok": False}, 0)
    assert not deve_repetir_feed({"http": 429}, 2)
    print("coleta_noticias: ok")


def main():
    if "--self-test" in sys.argv:
        _self_test()
        return
    horas = 12
    for arg in sys.argv[1:]:
        if arg.isdigit():
            horas = int(arg)
    saida = coletar(horas)
    print(json.dumps(saida, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()


"""Utilitários compartilhados pelos geradores que usam modelos_ia (OpenAI).

Não imprime chaves nem tokens. Comentários em português só onde a intenção
não é óbvia no código.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRT = timezone(timedelta(hours=-3))
WD = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)
HORA_RE = re.compile(r"^([01]?\d|2[0-3])h([0-5]\d)?$")
HORA_COLON_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DATA_RE = re.compile(r"^\d{1,2}/\d{1,2}(/\d{2,4})?$")
HORA_NO_TEXTO_RE = re.compile(
    r"(?:às|as)\s+(\d{1,2})h(?:([0-5]\d))?"
    r"|(\d{1,2})h([0-5]\d)?"
    r"|(\d{1,2}):([0-5]\d)",
    re.I,
)
TRAVESSAO_RE = re.compile(r"[—–]")
# Menos tipográfico (U+2212), usado nos deltas da análise: permitido.
MENOS = "\u2212"


def agora_sp(agora=None):
    if agora is None:
        agora = datetime.now(BRT)
    elif agora.tzinfo is None:
        agora = agora.replace(tzinfo=BRT)
    else:
        agora = agora.astimezone(BRT)
    return agora


def weekday_de(dia):
    return WD[dia.weekday()]


def ler_json(path, padrao=None):
    p = Path(path)
    if not p.exists():
        return padrao
    return json.loads(p.read_text(encoding="utf-8"))


def texto(path, padrao=""):
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else padrao


def gravar_json(path, doc):
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sem_travessao(valor, *, preservar_abertura=False):
    """Troca travessão por hífen, salvo o ' — ' obrigatório da abertura do artigo."""
    if isinstance(valor, str):
        if preservar_abertura and valor.startswith("RIO DE JANEIRO, ") and " — " in valor[:60]:
            pre, resto = valor.split(" — ", 1)
            return pre + " — " + TRAVESSAO_RE.sub("-", resto)
        return TRAVESSAO_RE.sub("-", valor)
    if isinstance(valor, list):
        return [sem_travessao(x, preservar_abertura=preservar_abertura) for x in valor]
    if isinstance(valor, dict):
        return {k: sem_travessao(v, preservar_abertura=preservar_abertura) for k, v in valor.items()}
    return valor


def tem_travessao(obj, *, ignorar_abertura=False):
    if isinstance(obj, str):
        s = obj
        if ignorar_abertura and s.startswith("RIO DE JANEIRO, ") and " — " in s[:60]:
            s = s.split(" — ", 1)[1]
        return bool(TRAVESSAO_RE.search(s))
    if isinstance(obj, list):
        return any(tem_travessao(x, ignorar_abertura=ignorar_abertura) for x in obj)
    if isinstance(obj, dict):
        return any(tem_travessao(v, ignorar_abertura=ignorar_abertura) for v in obj.values())
    return False


def normalizar_hora(valor, texto_item=""):
    """Devolve 7h / 14h30, ou None se não for horário de relógio."""
    v = (valor or "").strip().lower().replace(" ", "")
    v = v.replace("hs", "h").replace("hrs", "h")
    m = HORA_COLON_RE.match(v)
    if m:
        h, mm = int(m.group(1)), m.group(2)
        return f"{h}h{mm}" if mm != "00" else f"{h}h"
    m = HORA_RE.match(v)
    if m:
        h, mm = int(m.group(1)), m.group(2)
        return f"{h}h{mm}" if mm and mm != "00" else f"{h}h"
    fonte = (valor or "") + " " + (texto_item or "")
    for m in HORA_NO_TEXTO_RE.finditer(fonte):
        if m.group(1) is not None:
            h, mm = int(m.group(1)), m.group(2)
        elif m.group(3) is not None:
            h, mm = int(m.group(3)), m.group(4)
        else:
            h, mm = int(m.group(5)), m.group(6)
        if 0 <= h <= 23:
            return f"{h}h{mm}" if mm and mm != "00" else f"{h}h"
    return None


def limpar_agenda(agenda):
    """Agenda só com hora marcada; descarta data no lugar da hora (11/11)."""
    out = []
    for item in agenda or []:
        if not isinstance(item, dict):
            continue
        texto_item = item.get("text") or item.get("texto") or ""
        bruto = item.get("time") or item.get("hora") or ""
        hora = normalizar_hora(bruto, texto_item)
        if not hora:
            print(f"aviso: item de agenda sem hora marcada ignorado (time={bruto!r})", file=sys.stderr)
            continue
        src = item.get("source") or item.get("fonte") or {}
        if not isinstance(src, dict):
            src = {}
        out.append({
            "time": hora,
            "text": sem_travessao(texto_item).strip(),
            "source": {
                "name": (src.get("name") or src.get("nome") or "").strip(),
                "url": (src.get("url") or "").strip(),
            },
        })
    return out


def n_frases(texto_item):
    t = (texto_item or "").strip()
    if not t:
        return 0
    partes = re.findall(r"[^.!?]+[.!?]+", t)
    return len(partes) if partes else 1


def urls_de(*blocos):
    urls = set()

    def andar(obj):
        if isinstance(obj, dict):
            u = obj.get("url")
            if isinstance(u, str) and u.startswith("http"):
                urls.add(u)
            for v in obj.values():
                andar(v)
        elif isinstance(obj, list):
            for v in obj:
                andar(v)

    for b in blocos:
        andar(b)
    return urls


def filtrar_fontes(sources, permitidas, minimo=1, maximo=4):
    out, vistos = [], set()
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        url = (s.get("url") or "").strip()
        name = (s.get("name") or s.get("nome") or "").strip()
        if permitidas is not None and url not in permitidas:
            continue
        if not url or url in vistos:
            continue
        vistos.add(url)
        out.append({"name": name, "url": url})
        if len(out) >= maximo:
            break
    if len(out) < minimo:
        return []
    return out


def edicao_recente(atras=0):
    ix = ler_json(ROOT / "noticias" / "index.json") or {}
    dias = ix.get("editions") or []
    if len(dias) <= atras:
        return None, None
    nome = dias[-1 - atras]
    path = ROOT / "noticias" / f"{nome}.json"
    if not path.exists():
        return nome, None
    return nome, ler_json(path)


def resumir_noticias(edicao, limite=24):
    if not edicao:
        return [], []
    itens = []
    for it in edicao.get("items") or []:
        outlets = []
        for o in (it.get("outlets") or it.get("sources") or [])[:3]:
            if isinstance(o, dict) and (o.get("url") or o.get("name")):
                outlets.append({"name": o.get("name") or o.get("nome") or "", "url": o.get("url") or ""})
        itens.append({
            "id": it.get("id"),
            "segment": it.get("segment"),
            "score": it.get("score") if it.get("score") is not None else it.get("nota"),
            "title": it.get("title") or "",
            "summary": (it.get("summary") or "")[:500],
            "outlets": outlets,
            "desdobramento": it.get("desdobramento") or None,
        })
    itens.sort(key=lambda x: float(x["score"] or 0), reverse=True)
    trends = []
    for t in edicao.get("x_trends") or []:
        fonte = t.get("fonte") or t.get("source") or {}
        if not isinstance(fonte, dict):
            fonte = {}
        trends.append({
            "nome": t.get("nome") or t.get("name"),
            "sobre": t.get("sobre") or "",
            "leitura": t.get("leitura") or "",
            "fonte": {"name": fonte.get("name") or fonte.get("nome") or "", "url": fonte.get("url") or ""},
        })
    return itens[:limite], trends


def trends_arquivo():
    d = ler_json(ROOT / "noticias" / "trends.json")
    if not d:
        return None
    return {
        "coletado": d.get("coletado"),
        "fonte": d.get("fonte"),
        "trends": [{"nome": t.get("nome"), "volume": t.get("volume")} for t in (d.get("trends") or [])[:20]],
    }


def atualizar_index(pasta, dia, chave="days"):
    pasta = Path(pasta)
    path = pasta / "index.json"
    ix = ler_json(path) or {}
    days = list(ix.get(chave) or [])
    marca = dia.isoformat() if hasattr(dia, "isoformat") else str(dia)
    if marca not in days:
        days.append(marca)
    ix[chave] = sorted(set(days))
    gravar_json(path, ix)
    return path


def escrever_output(**kwargs):
    dest = os.environ.get("GITHUB_OUTPUT")
    if not dest:
        return
    with open(dest, "a", encoding="utf-8") as fh:
        for k, v in kwargs.items():
            fh.write(f"{k}={v}\n")


def abertura_dateline(agora=None):
    agora = agora_sp(agora)
    return f"RIO DE JANEIRO, {agora.day} de {MESES[agora.month - 1]} de {agora.year} — "


def repo_nome():
    return os.environ.get("GITHUB_REPOSITORY") or "rogeriotpires-spec/instagram-dashboard"


def gh_token():
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def gh_api(path, method="GET", body=None):
    """Chama a API do GitHub. Não registra o token."""
    token = gh_token()
    if not token:
        raise RuntimeError("sem GH_TOKEN/GITHUB_TOKEN para falar com o GitHub")
    url = path if path.startswith("http") else f"https://api.github.com/repos/{repo_nome()}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "painel-rogerrio",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as e:
        detalhe = e.read()[:200].decode("utf-8", "replace")
        raise RuntimeError(f"GitHub HTTP {e.code}: {detalhe}") from None


def issues_abertas(prefixo, autor="rogeriotpires-spec"):
    dados = gh_api("/issues?state=open&per_page=50")
    out = []
    for it in dados or []:
        if it.get("pull_request"):
            continue
        titulo = it.get("title") or ""
        login = ((it.get("user") or {}).get("login") or "")
        if titulo.startswith(prefixo) and login == autor:
            out.append(it)
    return out


def fechar_issue(numero, comentario):
    gh_api(f"/issues/{numero}/comments", method="POST", body={"body": comentario})
    gh_api(f"/issues/{numero}", method="PATCH", body={"state": "closed"})


_SEGURO_CAMINHO = "/%:@&=+$,;~()*!'"
_SEGURO_QUERY = "/%:@&=+$,;~()*!'?"


def url_ascii(url):
    """Devolve a URL só com ASCII: host em IDNA e caminho/query em %XX (UTF-8).

    Mantém os %XX que já existem (o "%" é seguro), então URL já codificada não
    muda. Ex.: .../acordo-de-cooperação → .../acordo-de-coopera%C3%A7%C3%A3o.
    """
    if not url:
        return url
    try:
        url.encode("ascii")
        return url
    except UnicodeEncodeError:
        pass
    p = urllib.parse.urlsplit(url.strip())
    host = p.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        host = urllib.parse.quote(host, safe="")
    netloc = host
    if p.port:
        netloc = f"{netloc}:{p.port}"
    if p.username:
        cred = urllib.parse.quote(p.username, safe="%")
        if p.password:
            cred += ":" + urllib.parse.quote(p.password, safe="%")
        netloc = f"{cred}@{netloc}"
    return urllib.parse.urlunsplit((
        p.scheme,
        netloc,
        urllib.parse.quote(p.path, safe=_SEGURO_CAMINHO, encoding="utf-8"),
        urllib.parse.quote(p.query, safe=_SEGURO_QUERY, encoding="utf-8"),
        urllib.parse.quote(p.fragment, safe=_SEGURO_QUERY, encoding="utf-8"),
    ))


def ler_pagina(url, limite=4000):
    """Leitura simples de uma URL pública. Não tenta o G1 (bloqueia)."""
    if not url or not url.startswith("http"):
        return None
    if "g1.globo.com" in url:
        return None
    try:
        # URL com acento (ç, ã) quebrava com UnicodeEncodeError: codifica antes.
        # O dict devolvido mantém a url original, a mesma que veio no pedido.
        req = urllib.request.Request(url_ascii(url), headers={"User-Agent": "Mozilla/5.0 PainelRogerrio/1.0", "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read(80_000).decode("utf-8", "replace")
    except Exception as e:
        print(f"aviso: não li {url}: {type(e).__name__}", file=sys.stderr)
        return None
    titulo = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I) or \
        re.search(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']', html, re.I)
    def limpa(s):
        s = re.sub(r"<[^>]+>", "", s or "")
        return re.sub(r"\s+", " ", s).strip()
    return {
        "url": url,
        "title": limpa(titulo.group(1) if titulo else "")[:200],
        "description": limpa(desc.group(1) if desc else "")[:limite],
        "published": extrair_publicado(html),
    }



def parse_iso(valor):
    if not valor or not isinstance(valor, str):
        return None
    v = valor.strip()
    try:
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BRT)
    return dt.astimezone(BRT)


def fresco(published, agora=None, horas=3):
    agora = agora_sp(agora)
    dt = parse_iso(published)
    if not dt:
        return False
    return dt <= agora and (agora - dt) <= timedelta(hours=horas)


def extrair_publicado(html):
    if not html:
        return None
    pats = [
        r'property=["\']article:published_time["\'][^>]*content=["\']([^"\']+)',
        r'content=["\']([^"\']+)["\'][^>]*property=["\']article:published_time',
        r'property=["\']og:published_time["\'][^>]*content=["\']([^"\']+)',
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'<time[^>]+datetime=["\']([^"\']+)',
    ]
    for pat in pats:
        m = re.search(pat, html, re.I)
        if m and parse_iso(m.group(1)):
            return parse_iso(m.group(1)).isoformat()
    return None


def ler_pagina_com_data(url, limite=4000):
    info = ler_pagina(url, limite=limite)
    if not info:
        return None
    # relê o HTML só para a data: ler_pagina já descarta o HTML. Faz de novo se preciso.
    # Para não duplicar o fetch, tentamos um campo publicado se a descrição trouxer ISO.
    return info


def normalizar_option(opt, tipo, permitidas, *, slides_objetos=False):
    """Normaliza uma option de pauta do Instagram (tipos i, r, c, s)."""
    if not isinstance(opt, dict):
        raise ValueError("option não é objeto")
    tipo = (tipo or opt.get("type") or "").strip().lower()
    title = sem_travessao((opt.get("title") or "").strip())
    fact = sem_travessao((opt.get("fact") or "").strip())
    if not title or not fact:
        raise ValueError("option sem title ou fact")
    fontes = filtrar_fontes(opt.get("sources") or opt.get("fontes") or [], permitidas, minimo=1, maximo=4)
    if not fontes:
        raise ValueError(f"option '{title}' ficou sem fonte válida")
    risk = (opt.get("risk") or "").strip().lower()
    if risk == "medio":
        risk = "médio"
    if risk not in {"baixo", "médio", "alto"}:
        raise ValueError(f"risk inválido em '{title}': {risk}")
    tl = opt.get("timeline") or {}
    if not isinstance(tl, dict):
        tl = {}
    rende_bruto = tl.get("rende")
    if isinstance(rende_bruto, str) and rende_bruto.lower() in {"medio", "médio", "alto", "baixo", "sim", "nao", "não"}:
        # no alerta quente, rende é um rótulo; nas options, é booleano
        rende = rende_bruto.lower() not in {"nao", "não", "baixo"}
        tl_out = {"rende": rende_bruto if tipo == "quente" else rende}
        if rende:
            tl_out["angle"] = sem_travessao((tl.get("angle") or "").strip())
        else:
            tl_out["motivo"] = sem_travessao((tl.get("motivo") or tl.get("angle") or "").strip()) or "Não sustenta artigo agora."
    else:
        rende = bool(rende_bruto)
        tl_out = {"rende": rende}
        if rende:
            tl_out["angle"] = sem_travessao((tl.get("angle") or "").strip())
            if not tl_out["angle"]:
                raise ValueError(f"timeline.angle vazio em '{title}'")
        else:
            tl_out["motivo"] = sem_travessao((tl.get("motivo") or tl.get("angle") or "").strip()) or "Não sustenta artigo agora."
    out = {
        "type": tipo,
        "title": title,
        "tone": sem_travessao((opt.get("tone") or "").strip()),
        "fact": fact,
        "sources": fontes,
        "caption": sem_travessao((opt.get("caption") or "").strip()),
        "risk": risk,
        "risk_note": sem_travessao((opt.get("risk_note") or "").strip()),
        "timeline": tl_out,
    }
    if opt.get("duration"):
        out["duration"] = sem_travessao(str(opt.get("duration")).strip())
    if opt.get("desdobramento"):
        out["desdobramento"] = sem_travessao(str(opt.get("desdobramento")).strip())
    if tipo == "i":
        estilo = (opt.get("style") or "").strip()
        if estilo not in {"ilustracao", "realista", "foto-oficial", "cartaz", "print"}:
            raise ValueError(f"style inválido em '{title}': {estilo}")
        art = opt.get("art_text") or []
        if isinstance(art, str):
            art = [art]
        out["style"] = estilo
        out["art_text"] = [sem_travessao(str(x)) for x in art if str(x).strip()]
        out["faces"] = sem_travessao((opt.get("faces") or "").strip())
        out["prompt"] = sem_travessao((opt.get("prompt") or "").strip())
        if not out["prompt"] or not out["art_text"]:
            raise ValueError(f"imagem '{title}' sem prompt ou art_text")
    elif tipo == "r":
        hooks = opt.get("hooks") or []
        if hooks:
            out["hooks"] = [sem_travessao(str(x)) for x in hooks[:4]]
        out["script"] = sem_travessao((opt.get("script") or "").strip())
        st = opt.get("screen_text") or []
        if isinstance(st, str):
            st = [st]
        out["screen_text"] = [sem_travessao(str(x)) for x in st if str(x).strip()][:4]
        if not out["script"]:
            raise ValueError(f"reel '{title}' sem script")
    elif tipo == "c":
        slides = opt.get("slides") or []
        if isinstance(slides, str):
            slides = [slides]
        out_slides = []
        for x in slides:
            if isinstance(x, dict):
                item = {"n": x.get("n") or (len(out_slides) + 1), "text": sem_travessao(str(x.get("text") or "").strip())}
                if x.get("note"):
                    item["note"] = sem_travessao(str(x.get("note")).strip())
                if item["text"]:
                    out_slides.append(item)
            elif str(x).strip():
                out_slides.append(sem_travessao(str(x).strip()))
        if not (3 <= len(out_slides) <= 10):
            raise ValueError(f"carrossel '{title}' precisa de 3 a 10 slides (veio {len(out_slides)})")
        out["slides"] = out_slides
        if opt.get("style"):
            out["style"] = (opt.get("style") or "").strip()
        if opt.get("prompt"):
            out["prompt"] = sem_travessao((opt.get("prompt") or "").strip())
        if opt.get("art_text"):
            art = opt.get("art_text")
            if isinstance(art, str):
                art = [art]
            out["art_text"] = [sem_travessao(str(x)) for x in art]
        if opt.get("faces"):
            out["faces"] = sem_travessao((opt.get("faces") or "").strip())
    elif tipo == "s":
        poll = opt.get("poll") or {}
        if not isinstance(poll, dict) or not poll.get("question") or not poll.get("options"):
            raise ValueError(f"stories '{title}' sem poll")
        opcoes = [sem_travessao(str(x)) for x in (poll.get("options") or []) if str(x).strip()]
        if len(opcoes) < 2:
            raise ValueError(f"stories '{title}' precisa de 2 opções na enquete")
        out["poll"] = {"question": sem_travessao(str(poll.get("question")).strip()), "options": opcoes[:4]}
        if opt.get("slides"):
            slides = opt.get("slides")
            if isinstance(slides, str):
                slides = [slides]
            out["slides"] = [sem_travessao(str(x)) for x in slides if str(x).strip()]
        if opt.get("style"):
            out["style"] = (opt.get("style") or "").strip()
    else:
        raise ValueError(f"tipo de slot desconhecido: {tipo}")
    if not out.get("caption"):
        raise ValueError(f"option '{title}' sem caption")
    return out


def fmt_br(n):
    if n is None:
        return "sem dado"
    n = float(n)
    sig = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        x = n / 1_000_000
        s = f"{x:.2f}".rstrip("0").rstrip(".")
        return sig + s.replace(".", ",") + " mi"
    if n >= 100_000:
        return sig + f"{n/1000:.0f}".replace(".", ",") + " mil"
    if n >= 1000:
        inteiro = f"{int(round(n)):,}".replace(",", ".")
        return sig + inteiro
    if n == int(n):
        return sig + str(int(n))
    return sig + f"{n:.1f}".replace(".", ",")


def fmt_delta(pct):
    if pct is None:
        return "sem comparação", "na"
    if pct == 0:
        return "0%", "na"
    sinal = "+" if pct > 0 else MENOS
    val = abs(float(pct))
    txt = f"{val:.1f}".rstrip("0").rstrip(".")
    txt = txt.replace(".", ",")
    return f"{sinal}{txt}%", ("up" if pct > 0 else "down")


def _self_test():
    assert weekday_de(datetime(2026, 11, 20).date()) == "Sex"
    assert normalizar_hora("10h") == "10h"
    assert normalizar_hora("14:30") == "14h30"
    assert normalizar_hora("11/11") is None
    assert normalizar_hora("8/11", "começa às 14h30") == "14h30"
    ag = limpar_agenda([
        {"time": "10h", "text": "STF julga.", "source": {"name": "A", "url": "https://a.com/x"}},
        {"time": "11/11", "text": "Sem hora.", "source": {"name": "B", "url": "https://b.com/x"}},
    ])
    assert [a["time"] for a in ag] == ["10h"]
    assert sem_travessao("um — dois") == "um - dois"
    assert " — " in abertura_dateline(datetime(2026, 11, 19, 10, tzinfo=BRT))
    assert abertura_dateline(datetime(2026, 11, 19, 10, tzinfo=BRT)).startswith("RIO DE JANEIRO, 19 de novembro de 2026")
    assert n_frases("Uma. Duas! Três?") == 3
    assert fresco("2026-11-19T10:00:00-03:00", datetime(2026, 11, 19, 12, 0, tzinfo=BRT))
    assert not fresco("2026-11-19T08:00:00-03:00", datetime(2026, 11, 19, 12, 0, tzinfo=BRT))
    assert fmt_br(1_800_000) == "1,8 mi"
    assert fmt_br(3277) == "3.277"
    d, dire = fmt_delta(-14)
    assert d.startswith(MENOS) and d.endswith("14%")
    assert dire == "down"
    opt = normalizar_option({
        "type": "r", "title": "Senado aprova o ajuste fiscal", "tone": "firme",
        "duration": "45s", "fact": "O Senado aprovou o texto e enviou à Câmara.",
        "sources": [{"name": "A", "url": "https://ex.com/a"}],
        "hooks": ["O Senado votou o ajuste."],
        "script": "O Senado aprovou o ajuste fiscal e o texto vai à Câmara. Você acompanha?",
        "screen_text": ["Ajuste fiscal aprovado"],
        "caption": "O Senado aprovou o ajuste. Comenta aqui.",
        "risk": "baixo", "risk_note": "Sem crime envolvido.",
        "timeline": {"rende": True, "angle": "O ajuste fiscal aprovado no Senado."},
    }, "r", {"https://ex.com/a"})
    assert opt["type"] == "r" and opt["script"]
    # URL com acento vira ASCII válido; URL já codificada não muda
    u = url_ascii("https://www.dw.com/pt-br/acordo-de-cooperação-militar/a-123?q=defesa nação#seção")
    u.encode("ascii")
    assert u == "https://www.dw.com/pt-br/acordo-de-coopera%C3%A7%C3%A3o-militar/a-123?q=defesa%20na%C3%A7%C3%A3o#se%C3%A7%C3%A3o", u
    assert url_ascii(u) == u
    ja = "https://www.dw.com/pt-br/acordo-de-coopera%C3%A7%C3%A3o-militar/a-123?x=1&y=a%2Fb"
    assert url_ascii(ja) == ja
    assert url_ascii("https://exemplo.com/a-b_c(1)?a=1&b=2") == "https://exemplo.com/a-b_c(1)?a=1&b=2"
    assert url_ascii("https://notícias.com.br:8080/ação") == "https://xn--notcias-9ya.com.br:8080/a%C3%A7%C3%A3o"
    assert url_ascii("https://exemplo.com/coopera%C3%A7ão") == "https://exemplo.com/coopera%C3%A7%C3%A3o"
    print("ia_comum: ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print("uso: python3 tools/ia_comum.py --self-test", file=sys.stderr)
        sys.exit(2)

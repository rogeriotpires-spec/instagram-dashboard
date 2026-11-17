"""Leitura direta do RSS/Atom de um portal.

urllib primeiro, com nova tentativa em 429 e 5xx (espera crescente e jitter).
Se o urllib falhar e o Scrapling estiver instalado, tenta por ele.
Devolve o detalhe (ok, itens, erro, http) para o diagnóstico da coleta.
"""
import html
import random
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

try:
    from scrapling.fetchers import Fetcher
except Exception:
    Fetcher = None

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def _tag(el):
    if not isinstance(el.tag, str):
        return ""
    return el.tag.split("}")[-1].split(":")[-1]


def _data(texto, BRT, agora):
    if not texto:
        return None
    texto = texto.strip()
    d = None
    try:
        d = parsedate_to_datetime(texto)
    except Exception:
        try:
            d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=BRT)
    d = d.astimezone(BRT)
    if d > agora + timedelta(minutes=10):
        d -= timedelta(hours=3)
    return d


def _titulo(texto):
    t = html.unescape(texto or "")
    t = t.replace("<![CDATA[", "").replace("]]>", "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _baixar(url, tentativas=4):
    ultimo = "sem resposta"
    status = None
    for i in range(tentativas):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=22) as r:
                return r.status, r.read(1_500_000), None
        except urllib.error.HTTPError as e:
            status = e.code
            ultimo = f"HTTP {e.code}"
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra and str(ra).isdigit():
                    espera = min(40, int(ra))
                else:
                    espera = min(40, 1.4 * (2 ** i)) + random.uniform(0.2, 0.9)
                time.sleep(espera)
                continue
            break
        except Exception as e:
            ultimo = f"{type(e).__name__}: {str(e)[:100]}"
            time.sleep(min(20, 1.2 * (2 ** i)) + random.uniform(0, 0.5))
    if Fetcher is not None and status in (None, 403, 401):
        try:
            resp = Fetcher.get(url, timeout=20)
            corpo = getattr(resp, "body", None)
            if isinstance(corpo, str):
                corpo = corpo.encode("utf-8")
            if getattr(resp, "status", None) == 200 and corpo:
                return 200, bytes(corpo), None
            ultimo = f"scrapling HTTP {getattr(resp, 'status', '?')}"
        except Exception as e:
            ultimo = f"scrapling {type(e).__name__}: {str(e)[:80]}"
    return status, b"", ultimo


def _parse(dados):
    cabeca = dados[:240].lower()
    if b"iso-8859-1" in cabeca or b"latin-1" in cabeca:
        texto = dados.decode("iso-8859-1", "replace")
    else:
        try:
            texto = dados.decode("utf-8")
        except UnicodeDecodeError:
            texto = dados.decode("iso-8859-1", "replace")
        if texto.count("\ufffd") >= 3:
            texto = dados.decode("iso-8859-1", "replace")
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", texto)
    try:
        return ET.fromstring(texto)
    except ET.ParseError:
        try:
            from lxml import etree
            return etree.fromstring(texto.encode("utf-8"), etree.XMLParser(recover=True))
        except Exception:
            return None


def _itens(raiz, corte, BRT, agora, limite):
    saida = []
    for it in raiz.iter():
        if _tag(it) not in ("item", "entry"):
            continue
        campos = {}
        link = ""
        for f in list(it):
            t = _tag(f)
            if t == "link":
                href = f.get("href") or (f.text or "").strip()
                rel = (f.get("rel") or "alternate").lower()
                if href and (not link or rel == "alternate"):
                    link = href
            elif f.text and t not in campos:
                campos[t] = f.text.strip()
        titulo = _titulo(campos.get("title", ""))
        dt = _data(campos.get("pubDate") or campos.get("published") or campos.get("updated") or campos.get("date"), BRT, agora)
        if not titulo or not dt or dt < corte or dt > agora + timedelta(minutes=30):
            continue
        desc = _titulo(campos.get("description") or campos.get("summary") or campos.get("content") or campos.get("encoded") or "")
        if desc.lower() == titulo.lower() or len(desc) < 40:
            desc = ""
        saida.append({
            "titulo": titulo,
            "descricao": desc[:500],
            "quando": dt.strftime("%d/%m %H:%M"),
            "link": link,
            "_epoch": dt.timestamp(),
        })
    saida.sort(key=lambda x: x["_epoch"], reverse=True)
    return saida[:limite]


def ler_status(url, corte, BRT, agora, limite=25):
    status, dados, erro = _baixar(url)
    if not dados:
        return {"ok": False, "itens": [], "erro": erro or "vazio", "http": status}
    raiz = _parse(dados)
    if raiz is None:
        return {"ok": False, "itens": [], "erro": "XML inválido", "http": status}
    itens = _itens(raiz, corte, BRT, agora, limite)
    if not itens:
        # feed abriu, mas nada na janela: não é falha de rede
        return {"ok": True, "itens": [], "erro": None, "http": status, "vazio": True}
    return {"ok": True, "itens": itens, "erro": None, "http": status}


def ler(url, corte, BRT, agora, limite=25):
    """Compatível com o leitor antigo: lista ou None."""
    st = ler_status(url, corte, BRT, agora, limite)
    return st["itens"] or None

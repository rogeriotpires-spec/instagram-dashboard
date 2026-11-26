"""Repara uma edição antes de publicar: links, fatos repetidos, trends, títulos, blocos e tetos.

Uso:
  python3 tools/noticias_reparar.py noticias/AAAA-MM-DD-HHh.json [--aplicar]
  python3 tools/noticias_reparar.py --self-test
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import noticias_notas as notas

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; painel-noticias/1.0; +https://rogeriotpires-spec.github.io/instagram-dashboard/)"}
BATCHEXECUTE = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_SG = re.compile(r'data-n-a-sg="([^"]+)"')
_TS = re.compile(r'data-n-a-ts="([^"]+)"')
_OPINIAO = (
    "perde tempo", "e hora de", "é hora de", "precisa entender", "deveria",
    "a verdade sobre", "o que a midia esconde", "o que a mídia esconde",
    "chega de", "nao se deixe", "não se deixe",
)
_SUFIXO_PORTAL = re.compile(
    r"\s+(?:UOL Notícias|UOL|G1|Estadão|Estadao|Folha de S\.? ?Paulo|O Globo|CNN Brasil|"
    r"R7|Veja|Metrópoles|Metropoles|Poder360|Agência Brasil|Agencia Brasil|Meia Hora|"
    r"Extra|O Dia|Valor(?: Econômico)?|BandNews|SBT News)\.?\s*$",
    re.I,
)
_CLIMA = ("chuvas", "chuva forte", "temporal", "temporais", "terremoto", "enchente", "deslizamento", "alagamento")
_CRIME = ("prisao", "preso", "homicidio", "assassinato", "trafico", "faccao", "milicia", "estupro", "operacao policial")
_DESDOBRAMENTO_LIXO = ("assista ao vivo", "ao vivo", "veja o video", "veja o vídeo")


def _get(url, timeout=8, method="GET", data=None, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.read()


def desenrolar_url(url):
    """Tira redirecionadores conhecidos (Folha e similares)."""
    u = (url or "").strip()
    if not u:
        return u
    if "redir.folha" in u and "*" in u:
        u = u.split("*", 1)[1]
    return u


def _id_google(url):
    try:
        p = urllib.parse.urlparse(url)
        partes = p.path.split("/")
        if p.hostname == "news.google.com" and len(partes) > 1 and partes[-2] in ("articles", "read"):
            return partes[-1] or None
    except ValueError:
        return None
    return None


def _pagina_params(art_id):
    return (
        f"https://news.google.com/rss/articles/{art_id}"
        "?hl=pt-BR&gl=BR&ceid=BR%3Apt-419"
    )


def _assinatura(html):
    sg, ts = _SG.search(html or ""), _TS.search(html or "")
    if sg and ts:
        return sg.group(1), ts.group(1)
    return None


def _buscar_assinatura(art_id):
    try:
        _, _, corpo = _get(_pagina_params(art_id), timeout=8)
        parsed = _assinatura(corpo.decode("utf-8", "replace"))
        if parsed:
            return art_id, parsed[0], parsed[1]
    except Exception:
        return None
    return None


def _corpo_batchexecute(itens):
    ctx = [
        ["X", "X", ["X", "X"], None, None, 1, 1, "BR:pt-419", None, 1, None, None, None, None, None, 0, 1],
        "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
    ]
    envelopes = []
    for req_id, art_id, ts, sig in itens:
        inner = ["garturlreq", ctx, art_id, int(ts) if str(ts).isdigit() else ts, sig]
        envelopes.append(["Fbv4je", json.dumps(inner, separators=(",", ":")), None, str(req_id)])
    return ("f.req=" + urllib.parse.quote(json.dumps([envelopes], separators=(",", ":")))).encode()


def _ler_batchexecute(texto):
    body = texto
    if "\n\n" in body:
        body = body.split("\n\n", 1)[1]
    body = body.lstrip()
    if body.startswith(")]}'"):
        body = body.split("\n", 1)[1] if "\n" in body else body[4:]
        body = body.lstrip()
    rows = json.loads(body)
    if rows and rows[-1] and isinstance(rows[-1], list) and rows[-1][0] == "di":
        rows = rows[:-1]
    if rows and isinstance(rows[-1], list) and rows[-1] and rows[-1][0] == "e":
        rows = rows[:-1]
    pares = {}
    for row in rows:
        if not (isinstance(row, list) and len(row) >= 3):
            continue
        payload = row[2]
        if row[0] == "wrb.fr" or (len(row) > 1 and row[1] == "Fbv4je"):
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, list) and payload and payload[0] == "garturlres":
                req_id = None
                for cell in reversed(row[3:]):
                    if cell is not None:
                        req_id = str(cell)
                        break
                if req_id is not None:
                    pares[req_id] = payload[1]
    return pares


def decodificar_google(urls, max_workers=8):
    """Resolve um lote de URLs do Google Notícias para o endereço do portal.

    Devolve {url_original: url_do_portal} só para as que deram certo.
    """
    unicas = []
    vistos = set()
    for u in urls:
        if u and u not in vistos and _id_google(u):
            vistos.add(u)
            unicas.append(u)
    if not unicas:
        return {}
    pendentes = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for resultado in ex.map(_buscar_assinatura, [_id_google(u) for u in unicas]):
            if resultado:
                pendentes.append(resultado)
    if not pendentes:
        return {}
    id_para_orig = {}
    for u in unicas:
        id_para_orig.setdefault(_id_google(u), u)
    itens = []
    for i, (art_id, sig, ts) in enumerate(pendentes):
        itens.append((str(i), art_id, ts, sig))
    mapa_i = {str(i): pendentes[i][0] for i in range(len(pendentes))}
    saida = {}
    # lotes de 20 para não estourar o POST
    for inicio in range(0, len(itens), 20):
        lote = itens[inicio:inicio + 20]
        try:
            _, _, corpo = _get(
                BATCHEXECUTE,
                timeout=15,
                method="POST",
                data=_corpo_batchexecute(lote),
                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            )
            pares = _ler_batchexecute(corpo.decode("utf-8", "replace"))
        except Exception:
            continue
        for req_id, dest in pares.items():
            art_id = mapa_i.get(req_id)
            orig = id_para_orig.get(art_id)
            if orig and dest and "news.google.com" not in dest:
                saida[orig] = dest
    return saida


def tokens_slug(url):
    path = urllib.parse.urlparse(url or "").path
    path = urllib.parse.unquote(path)
    # tira id numérico solto no fim
    partes = re.findall(r"[a-z0-9]{3,}", notas.norm(path.replace("-", " ")))
    return {w for w in partes if w not in notas.STOP and not w.isdigit() and len(w) >= 4}


def link_combina_fato(url, titulo, resumo=""):
    """Verdadeiro quando o endereço (slug) parece o mesmo fato do título."""
    u = desenrolar_url(url)
    if not u or "news.google.com" in u:
        return False
    slug = tokens_slug(u)
    fato = {w for w in notas.tokens(titulo) if w not in notas.UBIQUOS and len(w) >= 4}
    if resumo:
        fato |= {w for w in notas.tokens(resumo[:180]) if w not in notas.UBIQUOS and len(w) >= 4}
    if not slug or not fato:
        # slug genérico (home, /noticias): não dá para confirmar, cai fora
        return False
    comuns = slug & fato
    if len(comuns) >= 2:
        return True
    if len(comuns) == 1 and len(slug) <= 4:
        # um nome próprio longo no slug (bolsonaro, tupancireta) basta se o slug for curto
        return any(len(w) >= 7 for w in comuns)
    # sobreposição relativa ao menor conjunto
    return len(comuns) / max(1, min(len(slug), len(fato))) >= 0.4 and len(comuns) >= 1


def titulo_opiniao(titulo):
    t = notas.norm(titulo or "")
    return any(notas.norm(m) in t for m in _OPINIAO)


def _corrigir_titulo(item):
    original = item.get("title") or ""
    titulo = notas.limpa_manchete(original)
    item["title"] = titulo
    if not titulo_opiniao(titulo):
        return titulo != original.strip()
    resumo = item.get("summary") or ""
    frases = [p.strip() for p in re.split(r"[.!?]+", resumo) if p.strip()]
    for fr in frases:
        fr = _SUFIXO_PORTAL.sub("", fr).strip()
        if len(fr) >= 40 and not titulo_opiniao(fr):
            item["title"] = notas.limpa_manchete(fr)
            return True
    for o in item.get("outlets") or []:
        # não temos o título original do portal aqui; segue
        break
    # último recurso: tira o juízo curto no começo
    novo = re.sub(r"^.+? (perde tempo|deveria|é hora de)\s+", "", titulo, flags=re.I).strip()
    if novo and novo != titulo and not titulo_opiniao(novo) and len(novo) >= 25:
        item["title"] = notas.limpa_manchete(novo)
        return True
    return False


def _limpar_resumo(item):
    s = notas.limpa_texto_fonte(item.get("summary") or "")
    partes = []
    for fr in re.split(r"(?<=[.!?])\s+", s):
        fr = _SUFIXO_PORTAL.sub("", fr).strip()
        if not fr or notas.frase_lixo(fr):
            continue
        # frase que é só o nome do portal
        if re.fullmatch(r"(?:UOL(?: Notícias)?|G1|Estadão|Folha(?: de S\.? ?Paulo)?|O Globo|CNN Brasil|Meia Hora|R7|Veja|Metrópoles|Poder360|Agência Brasil|Extra|O Dia|Valor(?: Econômico)?)", fr.strip(" ."), flags=re.I):
            continue
        partes.append(fr)
    s = " ".join(partes).strip()
    s = re.sub(r"\s{2,}", " ", s)
    item["summary"] = s
    des = notas.norm(item.get("desdobramento") or "")
    if any(x in des for x in _DESDOBRAMENTO_LIXO):
        item.pop("desdobramento", None)
        return True
    return False


def resumo_curto(item, minimo_frases=5, minimo_chars=None):
    """Verdadeiro quando o resumo não tem as 5 frases pedidas na edição."""
    s = (item.get("summary") or "").strip()
    if not s:
        return True
    frases = [p.strip() for p in re.split(r"(?<=[.!?])\s+", s) if p.strip()]
    if not frases:
        frases = [p.strip() for p in re.split(r"[.!?]+", s) if p.strip()]
    if len(frases) >= minimo_frases:
        return False
    # Escape antigo (1 frase longa) só se o chamador baixar o mínimo explicitamente
    if minimo_chars is not None and len(s) >= minimo_chars and len(frases) >= 1 and minimo_frases <= 2:
        return False
    return True


def _ajustar_bloco(item):
    titulo = item.get("title") or ""
    resumo = item.get("summary") or ""
    urls = " ".join(o.get("url") or "" for o in item.get("outlets") or [])
    texto = titulo + " " + resumo
    t = notas.norm(texto)
    seg = item.get("segment") or ""
    novo = notas.ajustar_segmento(titulo, seg, "", urls, resumo)
    if any(notas.contem_termo(t, k) for k in _CLIMA) and not any(notas.contem_termo(t, k) for k in _CRIME):
        if novo == "seguranca" or (novo is None and seg == "seguranca"):
            if any(notas.contem_termo(t, k) for k in ("peru", "chile", "equador", "mexico", "méxico", "haiti")):
                novo = "internacional"
            elif notas.tem_sinal_rio(t):
                novo = "rio"
            # outro estado: não joga temporal para economia nem para o Rio
            elif notas.tem_outro_estado(t):
                novo = novo if novo and novo not in {"rio", "seguranca"} else None
    if novo and novo != seg:
        item["segment"] = novo
        return True
    return False


_SAUDE = {"hospital", "internado", "internacao", "internada", "uti", "cirurgia", "dores"}
_PESSOAS = {"bolsonaro", "lula", "michelle", "flavio", "moraes", "mendonca", "jair"}


def _mesmo_item(a, b):
    ta, tb = notas.tokens(a.get("title") or ""), notas.tokens(b.get("title") or "")
    if notas.mesmo_fato(ta, tb, a.get("title") or "", b.get("title") or ""):
        return True
    texto_a = (a.get("title") or "") + " " + (a.get("summary") or "")
    texto_b = (b.get("title") or "") + " " + (b.get("summary") or "")
    if notas.mesmo_assunto(texto_a, texto_b):
        return True
    # mesma pessoa + mesma internação (Bolsonaro no hospital com títulos diferentes)
    if (ta & _SAUDE) and (tb & _SAUDE) and (ta & tb & _PESSOAS):
        return not notas.conflito_numeros(a.get("title") or "", b.get("title") or "")
    return False


def fundir_itens(itens):
    """Junta notícias do mesmo fato e une os portais."""
    fundidos = []
    for it in itens:
        alvo = next((s for s in fundidos if _mesmo_item(it, s)), None)
        if alvo is None:
            fundidos.append(it)
            continue
        nomes = {o.get("name") for o in alvo.get("outlets") or []}
        for o in it.get("outlets") or []:
            if o.get("name") not in nomes and o.get("url"):
                alvo["outlets"].append(o)
                nomes.add(o.get("name"))
        # fica o título mais informativo e o resumo mais longo
        if len(it.get("title") or "") > len(alvo.get("title") or "") and not titulo_opiniao(it.get("title") or ""):
            alvo["title"] = it["title"]
        if len(it.get("summary") or "") > len(alvo.get("summary") or ""):
            alvo["summary"] = it["summary"]
        if it.get("score", 0) > alvo.get("score", 0):
            alvo["scores"] = it.get("scores") or alvo.get("scores")
        if it.get("id") and not alvo.get("id"):
            alvo["id"] = it["id"]
    return fundidos


def limpar_trends(trends, itens):
    vistos = set()
    ids = {it.get("id") for it in itens}
    por_id = {it.get("id"): it for it in itens}
    saida = []
    for t in trends or []:
        nome = (t.get("nome") or "").strip()
        chave = notas.norm(nome)
        if not nome or chave in vistos:
            continue
        uso = notas.norm(t.get("uso") or "")
        if "evitar foco" in uso or "evitar o foco" in uso:
            continue
        sobre = (t.get("sobre") or "").strip()
        chaves = {w for w in notas.tokens(nome) if len(w) >= 5}
        if chaves and sobre:
            if not (chaves & notas.tokens(sobre)) and notas.norm(sobre) != chave:
                # descrição de outro assunto (ex.: Russell descrito como Mendonça)
                if not chaves <= notas.tokens(sobre):
                    t = dict(t)
                    t["sobre"] = nome
                    t["item_id"] = None
                    sobre = nome
        item_id = t.get("item_id") or None
        if item_id and item_id not in ids:
            item_id = None
        if item_id and not notas.trend_casa_no_item(nome, por_id[item_id]):
            item_id = None
        if item_id is None:
            for it in itens:
                if notas.trend_casa_no_item(nome, it):
                    item_id = it.get("id")
                    break
        if item_id is None:
            # sem matéria correspondente, o trend não entra na edição
            continue
        if notas.eh_entretenimento(nome, "", sobre):
            continue
        ligado = por_id.get(item_id) or {}
        sobre = notas.sem_travessao((ligado.get("title") or sobre or nome).strip())
        uso = notas.uso_coerente(t.get("uso") or "", ligado)
        vistos.add(chave)
        saida.append({
            **t,
            "nome": nome,
            "sobre": sobre,
            "uso": uso,
            "item_id": item_id,
        })
        if len(saida) >= 8:
            break
    return saida


def recontar(item, cfg, tiers):
    outlets = item.get("outlets") or []
    pesos = {c["key"]: c["weight"] for c in cfg["criteria"]}
    scores = dict(item.get("scores") or {})
    scores["cobertura"] = notas.cobertura(len(outlets))
    scores["destaque"] = notas.destaque(outlets, tiers)
    if "persistencia" not in scores:
        scores["persistencia"] = 4
    if "peso_voce" not in scores:
        scores["peso_voce"] = notas.peso_voce(None, item.get("segment") or "")
    item["scores"] = scores
    item["score"] = notas.nota_final(scores, pesos)
    return item


def aplicar_tetos(itens, cfg):
    por = {}
    for it in itens:
        por.setdefault(it.get("segment") or "politica", []).append(it)
    teto = cfg.get("items_per_segment") or 5
    escolhidos = []
    for s in cfg.get("segments") or []:
        chave = s["key"]
        lista = sorted(por.get(chave) or [], key=lambda x: (-(x.get("score") or 0), -len(x.get("outlets") or [])))
        min_out = s.get("min_outlets", cfg.get("min_outlets") or 2)
        ok = []
        for it in lista:
            n = len(it.get("outlets") or [])
            if n >= min_out or (n >= 1 and s.get("min_outlets") == 1):
                ok.append(it)
        escolhidos.extend(ok[:teto])
    ordem = {s["key"]: i for i, s in enumerate(cfg.get("segments") or [])}
    escolhidos.sort(key=lambda it: (-(it.get("score") or 0), ordem.get(it.get("segment"), 99)))
    return escolhidos


def _limpar_outlets(item, resolvidos):
    kept, vistos = [], set()
    drop_google = 0
    drop_assunto = 0
    for o in item.get("outlets") or []:
        url = desenrolar_url(o.get("url") or "")
        if "news.google.com" in url:
            url = resolvidos.get(o.get("url") or "") or resolvidos.get(url) or ""
            if not url:
                drop_google += 1
                continue
        if not url or url in vistos:
            if not url:
                drop_google += 1
            continue
        if not link_combina_fato(url, item.get("title") or "", item.get("summary") or ""):
            drop_assunto += 1
            continue
        vistos.add(url)
        novo = dict(o)
        novo["url"] = url
        kept.append(novo)
    item["outlets"] = kept
    return drop_google, drop_assunto


def reparar(edicao, cfg, google=True):
    """Repara a edição no lugar e devolve (edicao, relatorio)."""
    itens = list(edicao.get("items") or [])
    rel = {
        "antes": len(itens),
        "links_google_resolvidos": 0,
        "links_google_descartados": 0,
        "links_fora_do_fato": 0,
        "fatos_fundidos": 0,
        "titulos_corrigidos": 0,
        "blocos_corrigidos": 0,
        "resumos_limpos": 0,
        "itens_curtos_removidos": 0,
        "trends_antes": len(edicao.get("x_trends") or []),
    }
    urls_g = [o.get("url") for it in itens for o in (it.get("outlets") or []) if _id_google(o.get("url") or "")]
    resolvidos = {}
    if google and urls_g:
        resolvidos = decodificar_google(urls_g)
        rel["links_google_resolvidos"] = len(resolvidos)
    for it in itens:
        g, f = _limpar_outlets(it, resolvidos)
        rel["links_google_descartados"] += g
        rel["links_fora_do_fato"] += f
        if _limpar_resumo(it):
            rel["resumos_limpos"] += 1
        if _corrigir_titulo(it):
            rel["titulos_corrigidos"] += 1
        if _ajustar_bloco(it):
            rel["blocos_corrigidos"] += 1
    n0 = len(itens)
    itens = fundir_itens(itens)
    rel["fatos_fundidos"] = n0 - len(itens)
    # tira item sem portal e resumo vazio quando houver folga
    vivos = []
    curtos = []
    for it in itens:
        if not (it.get("outlets") or []):
            continue
        if resumo_curto(it) or not (it.get("summary") or "").strip():
            curtos.append(it)
        else:
            vivos.append(it)
    # Resumo curto irreparável não volta só para cumprir o mínimo da seção.
    rel["itens_curtos_removidos"] += len(curtos)
    itens = vivos
    tiers = {o["name"]: o.get("tier") or 3 for o in cfg.get("outlets") or []}
    for it in itens:
        recontar(it, cfg, tiers)
    itens = aplicar_tetos(itens, cfg)
    for it in itens:
        recontar(it, cfg, tiers)
    edicao["items"] = itens
    edicao["x_trends"] = limpar_trends(edicao.get("x_trends") or [], itens)
    ids = {it.get("id") for it in itens}
    top = [i for i in (edicao.get("top") or []) if i in ids]
    if len(top) < (cfg.get("top_overall") or 5):
        resto = [it["id"] for it in itens if it.get("id") not in top]
        top.extend(resto[: (cfg.get("top_overall") or 5) - len(top)])
    edicao["top"] = top[: cfg.get("top_overall") or 5]
    if itens:
        edicao["headline"] = notas.sem_travessao("; ".join(it["title"] for it in itens[:3]))
    rel["depois"] = len(itens)
    rel["trends_depois"] = len(edicao.get("x_trends") or [])
    rel["links_depois"] = sum(len(it.get("outlets") or []) for it in itens)
    return edicao, rel


def conferir_graves(edicao, cfg):
    """Erros que impedem a publicação depois do reparo automático."""
    erros = []
    itens = edicao.get("items") or []
    if len(itens) < 8:
        erros.append(f"só {len(itens)} notícias")
    teto = cfg.get("items_per_segment") or 5
    por = {}
    vistos = []
    for it in itens:
        titulo = it.get("title") or ""
        por[it.get("segment")] = por.get(it.get("segment"), 0) + 1
        if por[it.get("segment")] > teto:
            erros.append(f"teto estourado em {it.get('segment')}: {por[it.get('segment')]}>{teto}")
        if titulo_opiniao(titulo):
            erros.append(f"título de opinião: {titulo}")
        if not (it.get("summary") or "").strip():
            erros.append(f"sem resumo: {titulo}")
        elif resumo_curto(it):
            erros.append(f"resumo curto: {titulo}")
        if "—" in json.dumps(it, ensure_ascii=False) or "–" in json.dumps(it, ensure_ascii=False):
            erros.append(f"travessão: {titulo}")
        for prev in vistos:
            if notas.mesmo_fato(notas.tokens(titulo), notas.tokens(prev), titulo, prev):
                erros.append(f"fato duplicado: «{titulo}» e «{prev}»")
        vistos.append(titulo)
    # trends duplicados
    nomes = [notas.norm(t.get("nome") or "") for t in edicao.get("x_trends") or []]
    if len(nomes) != len(set(nomes)):
        erros.append("trend repetido no X")
    return erros


def _self_test():
    assert desenrolar_url("https://redir.folha.com.br/redir/x/*https://www1.folha.uol.com.br/poder/abc.shtml") == "https://www1.folha.uol.com.br/poder/abc.shtml"
    assert _id_google("https://news.google.com/rss/articles/CBMihwFB?oc=5") == "CBMihwFB"
    assert link_combina_fato(
        "https://www.gazetaweb.com/noticias/politica/carlos-acompanha-bolsonaro-no-hospital-e-michelle-anuncia-volta-ao-brasil-950663",
        "Estado de saúde de Jair Bolsonaro volta a gerar preocupação e família acompanha internação",
    )
    assert not link_combina_fato(
        "https://www.jota.info/opiniao-e-analise/artigos/a-regra-dos-180-dias-como-o-prazo-semestral-do-cfm-trava-o-avanco-da-saude-digital",
        "Estado de saúde de Jair Bolsonaro volta a gerar preocupação e família acompanha internação",
    )
    assert not link_combina_fato(
        "https://tribunadonorte.com.br/natal/fab-confirma-testes-supersonicos-com-caca-gripen-apos-relatos-de-estrondo-na-grande-natal/",
        "Estado de saúde de Jair Bolsonaro volta a gerar preocupação",
    )
    assert titulo_opiniao("Lula perde tempo brigando com o mercado")
    assert not titulo_opiniao("Dólar fecha no maior valor desde abril de 2025")
    cfg = {
        "criteria": [{"key": "cobertura", "weight": 10}, {"key": "destaque", "weight": 8}, {"key": "persistencia", "weight": 6}, {"key": "peso_voce", "weight": 7}],
        "items_per_segment": 5,
        "top_overall": 5,
        "min_outlets": 2,
        "segments": [
            {"key": "politica", "min_items": 1},
            {"key": "economia", "min_items": 1},
        ],
        "outlets": [{"name": "UOL", "tier": 1}, {"name": "Estadão", "tier": 1}, {"name": "CNN Brasil", "tier": 1}],
    }
    ed = {
        "items": [
            {"id": "a", "segment": "politica", "title": "Jair Bolsonaro é internado após dores abdominais",
             "summary": "Jair Bolsonaro foi internado após sentir dores abdominais. A família acompanha o quadro no hospital. Michelle anunciou a volta ao Brasil. O ex-presidente permanece em observação. A equipe médica ainda não divulgou um boletim completo.",
             "scores": {}, "score": 8, "outlets": [
                 {"name": "UOL", "url": "https://noticias.uol.com.br/politica/ultimas-noticias/2026/11/21/bolsonaro-internado-dores-abdominais.htm"},
                 {"name": "Estadão", "url": "https://www.estadao.com.br/politica/jair-bolsonaro-da-entrada-em-hospital-apos-dores/"},
             ]},
            {"id": "b", "segment": "politica", "title": "Bolsonaro dá entrada em hospital após dores abdominais, diz Carlos",
             "summary": "Carlos Bolsonaro disse que o pai deu entrada no hospital após dores abdominais. O quadro segue em observação. A família acompanha o atendimento. Michelle deve voltar ao Brasil. A equipe médica ainda avalia o caso.",
             "scores": {}, "score": 8, "outlets": [
                 {"name": "CNN Brasil", "url": "https://www.cnnbrasil.com.br/politica/bolsonaro-hospital-dores-abdominais/"},
             ]},
            {"id": "c", "segment": "economia", "title": "Lula perde tempo brigando com o mercado",
             "summary": "O presidente Luiz Inacio Lula da Silva criticou agentes do mercado financeiro em entrevista. A equipe economica evitou comentar o episodio nesta sexta. O recado foi dado a empresários. O mercado reagiu com cautela. Assessores tentaram esclarecer o tom da fala.",
             "scores": {}, "score": 5, "outlets": [
                 {"name": "UOL", "url": "https://economia.uol.com.br/noticias/lula-critica-mercado-financeiro-entrevista.htm"},
                 {"name": "Estadão", "url": "https://www.estadao.com.br/economia/lula-critica-agentes-do-mercado-financeiro/"},
             ]},
        ],
        "x_trends": [
            {"nome": "Dia da Consciência Negra", "sobre": "Dia da Consciência Negra", "uso": "comentar", "item_id": None},
            {"nome": "Dia da Consciência Negra", "sobre": "Dia da Consciência Negra", "uso": "abordar", "item_id": None},
            {"nome": "Russell", "sobre": "Mendonça", "uso": "referenciar", "item_id": "a"},
            {"nome": "Flamengo", "sobre": "Flamengo", "uso": "evitar foco em esporte sem desdobramento político", "item_id": None},
            {"nome": "Bolsonaro", "sobre": "Jair Bolsonaro é internado após dores abdominais", "uso": "comentar o fato da edição", "item_id": "a"},
        ],
        "top": ["a", "b", "c"],
    }
    out, rel = reparar(ed, cfg, google=False)
    ids = [it["id"] for it in out["items"]]
    assert "b" not in ids or len(out["items"]) == 2, ids  # a e b fundidos
    assert rel["fatos_fundidos"] == 1
    titulos = [it["title"] for it in out["items"]]
    assert all(not titulo_opiniao(t) for t in titulos), titulos
    nomes = [t["nome"] for t in out["x_trends"]]
    assert "Dia da Consciência Negra" not in nomes  # sem matéria correspondente
    assert "Flamengo" not in nomes
    assert "Russell" not in nomes
    assert nomes.count("Bolsonaro") == 1
    graves = [e for e in conferir_graves(out, cfg) if not e.startswith("só ")]
    assert graves == [], graves

    # 1. resumo com 2 frases é curto
    assert resumo_curto({"summary": "O IBGE prorrogou as inscrições. A Bahia oferece mais vagas."})
    assert not resumo_curto({"summary": (
        "O IBGE prorrogou as inscrições para recenseadores. "
        "A Bahia é o estado com mais vagas. "
        "As provas ocorrem em dezembro. "
        "Os aprovados atuam no censo agropecuário. "
        "As inscrições terminam na próxima semana."
    )})

    # 2. hashtag e CTA saem do título/resumo
    it_yt = {
        "id": "pr-chuva", "segment": "rio",
        "title": "Temporais no PR atingem 50 cidades e deixam seis mortos. #JR24H #Shorts",
        "summary": "➡️ Acompanhe mais notícias em nosso site oficial: https://noticias.com/jr-na-tv Inscreva-se no canal.",
        "outlets": [{"name": "R7", "url": "https://r7.com/temporais-pr"}],
    }
    _corrigir_titulo(it_yt)
    _limpar_resumo(it_yt)
    assert "#JR24H" not in it_yt["title"] and "Shorts" not in it_yt["title"]
    assert "inscreva" not in notas.norm(it_yt["summary"])
    assert notas.ajustar_segmento(it_yt["title"], "rio") != "rio"

    # 3. três ângulos de tarifas viram um fato
    tarifas = [
        {"id": "a1", "title": "EUA propõem novas tarifas sobre produtos brasileiros, incluindo aeronaves e açúcar",
         "summary": "Os Estados Unidos propuseram novas tarifas sobre produtos brasileiros, incluindo aeronaves e açúcar. A alíquota atinge a Embraer.",
         "outlets": [{"name": "UOL", "url": "https://uol.com.br/tarifas-1"}]},
        {"id": "a2", "title": "Governo brasileiro manifesta indignação e não cederá a ameaças de tarifas dos EUA",
         "summary": "O governo manifestou indignação com as tarifas dos EUA sobre aeronaves e açúcar e disse que não cederá.",
         "outlets": [{"name": "G1", "url": "https://g1.globo.com/tarifas-2"}]},
        {"id": "a3", "title": "Governo Lula reage a ameaça de tarifas dos EUA e cita pressão sobre o Pix",
         "summary": "O governo Lula reagiu às tarifas dos EUA sobre aeronaves e açúcar e citou pressão sobre o Pix.",
         "outlets": [{"name": "Folha", "url": "https://folha.uol.com.br/tarifas-3"}]},
    ]
    fundidos = fundir_itens(tarifas)
    assert len(fundidos) == 1, [i["title"] for i in fundidos]
    assert len(fundidos[0]["outlets"]) == 3

    # 5. André não cola no Palmeiras; uso deslocado some
    itens_tr = [
        {"id": "pal", "title": "Palmeiras viaja para final da Libertadores com festa da torcida",
         "summary": "O Palmeiras embarcou com André e a delegação para o duelo."},
        {"id": "cor", "title": "Corinthians empata com Atlético-MG e sai da zona de rebaixamento",
         "summary": "O Corinthians empatou com o Atlético-MG em Belo Horizonte."},
    ]
    tr = limpar_trends([
        {"nome": "André", "sobre": "André", "uso": "Rebater com fato sobre indicações no governo Lula", "item_id": "pal"},
        {"nome": "Atlético Mineiro", "sobre": "Atlético Mineiro", "uso": "Explicar impacto do julgamento para eleições no Rio", "item_id": "cor"},
        {"nome": "Corinthians", "sobre": "Corinthians empata com Atlético-MG", "uso": "comentar o jogo", "item_id": "cor"},
    ], itens_tr)
    nomes = [x["nome"] for x in tr]
    assert "André" not in nomes
    atl = next(x for x in tr if x["nome"] == "Atlético Mineiro")
    assert "eleicoes" not in notas.norm(atl["uso"]) and "julgamento" not in notas.norm(atl["uso"])
    assert atl["item_id"] == "cor"

    print("noticias_reparar: ok")


def main():
    if "--self-test" in sys.argv:
        _self_test()
        return
    caminho = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "")
    if not caminho:
        raise SystemExit("informe o arquivo da edição")
    if not caminho.is_absolute():
        caminho = ROOT / caminho
    cfg = json.loads((ROOT / "noticias" / "config.json").read_text(encoding="utf-8"))
    edicao = json.loads(caminho.read_text(encoding="utf-8"))
    aplicar = "--aplicar" in sys.argv
    sem_google = "--sem-google" in sys.argv
    edicao, rel = reparar(edicao, cfg, google=not sem_google)
    erros = conferir_graves(edicao, cfg)
    print(json.dumps(rel, ensure_ascii=False))
    for e in erros:
        print("ERRO " + e)
    if aplicar:
        caminho.write_text(json.dumps(edicao, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"gravado {caminho.name}: {rel['depois']} notícias, {rel['links_depois']} links")
    sys.exit(1 if erros else 0)


if __name__ == "__main__":
    main()


"""Conferência editorial de uma edição contra a coleta que a gerou.

Cada frase do resumo precisa estar apoiada no título ou na descrição/lead das fontes do mesmo grupo.
O grupo não pode misturar fatos sem palavras em comum, o link tem de ser o
da coleta e a seção não pode ser a padrão errada (celebridade em Política).

Uso: python3 tools/qa_edicao.py noticias/AAAA-MM-DD-HHh.json coleta-noticias.json
Sai com código 1 se houver erro grave. O relatório vai para a saída padrão.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import noticias_notas as notas
import noticias_reparar as reparar

ROOT = Path(__file__).resolve().parent.parent

# Palavras gramaticais que, no fim de um resumo, indicam corte ("aparece como um.").
_FINAIS_FRACAS = {
    "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "que", "e", "o", "a",
    "os", "as", "para", "por", "com", "em", "no", "na", "ao", "como", "seu", "sua",
    "se", "ao", "à", "às", "disp",
}


def _frases(texto):
    return [p.strip() for p in re.split(r"[.!?]+", texto or "") if p.strip()]


def resumo_cortado(texto):
    """Verdadeiro quando o resumo parece truncado no meio da palavra ou da frase.

    Cobre o corte real da edição 25/11-06h ("O motivo do portugu." e
    "Rogério Ceni aparece como um.") e o caso sem pontuação final.
    """
    t = (texto or "").strip()
    if not t:
        return False
    if t[-1] not in ".!?…":
        return True
    partes = re.findall(r"[^.!?]+[.!?]+", t) or [t]
    ultima = partes[-1]
    palavras = re.findall(r"[A-Za-zÀ-ÿ0-9]+", ultima)
    if not palavras:
        return True
    ultima_palavra = notas.norm(palavras[-1])
    # Radical cortado: "portugu." termina em dígrafo típico de palavra incompleta.
    if len(ultima_palavra) >= 4 and re.search(r"(gu|nh|lh|qu)$", ultima_palavra):
        return True
    # Frase que morre numa palavra gramatical: "aparece como um."
    if len(palavras) >= 3 and ultima_palavra in _FINAIS_FRACAS:
        return True
    # Corte no meio da palavra ou em inicial solta ("filho de D.", "norte-americ.").
    # Número no fim ("transparência número 1.") é frase completa, não corte.
    if not ultima_palavra.isdigit():
        if len(ultima_palavra) == 1 and ultima_palavra.isalpha():
            return True
        if len(ultima_palavra) == 2 and ultima_palavra.isalpha() and ultima_palavra not in {
            "ja", "ha", "so", "eu", "se", "me", "si", "fe", "re", "ao", "as", "os",
            "um", "da", "de", "do", "no", "na", "em", "ou", "ai", "la", "li", "lu",
        }:
            return True
        # "norte-americ." / cortado numa consoante que quase não termina palavra em português.
        if len(ultima_palavra) >= 5 and re.search(r"[ck]$", ultima_palavra):
            return True
    if re.search(r"-\s*$", ultima.strip().rstrip(".!?")):
        return True
    if notas.frase_lixo(ultima):
        return True
    return False


def _texto_manchete(m):
    return notas.texto_apoio(m.get("titulo") or "", m.get("descricao") or "")


def assuntos_misturados(item, apoio):
    """Erro quando qualquer fonte de apoio não é o mesmo fato do título.

    Compara o título do item com o título e a descrição/lead da fonte, para
    não tratar ângulo diferente do mesmo fato (Corinthians vs Atlético no
    mesmo jogo; Lula sondou Trabuco vs 'executivo do Bradesco') como mistura.
    Mistura real (tarifaço + qualidade do ar em Manaus; Filosofia vs ensino
    integral) continua erro.
    """
    titulo = item.get("title") or ""
    if not titulo or not apoio:
        return None
    # Só os links que estão no item. Outra matéria do mesmo portal, puxada
    # para checar apoio de frase, não conta como mistura.
    urls = {o.get("url") for o in item.get("outlets") or []}
    fonte_item = [m for m in apoio if m.get("link") in urls] or list(apoio)
    base = notas.texto_apoio(titulo, "")
    base_tk = notas.tokens(base)
    estranhas = []
    vistos = set()
    for m in fonte_item:
        man = m.get("titulo") or ""
        if not man or man in vistos:
            continue
        fonte = _texto_manchete(m)
        if notas.mesmo_fato(base_tk, notas.tokens(fonte), base, fonte):
            continue
        # Dois nomes específicos compartilhados, sem conflito de número, são
        # o mesmo fato com redação muito diferente (Alerj/Assembleia do Rio).
        # Três ou mais (ex-noiva, facadas, matar) são o mesmo caso mesmo se
        # um texto cita a pena (28) e o outro a idade da vítima (22).
        inter = {w for w in (base_tk & notas.tokens(fonte)) if w not in notas.UBIQUOS and len(w) >= 4}
        if len(inter) >= 3 or (len(inter) >= 2 and not notas.conflito_numeros(base, fonte)):
            continue
        vistos.add(man)
        estranhas.append(man)
    if not estranhas:
        return None
    lista = " | ".join(x[:70] for x in estranhas[:4])
    return f"assuntos misturados em «{titulo}»: {lista}"


def erros_bloqueantes(erros):
    """Frase sem apoio, resumo cortado e assuntos misturados barram a publicação."""
    out = []
    for e in erros or []:
        el = e.lower()
        if (
            "frase sem manchete de apoio" in el
            or "frase sem apoio" in el
            or el.startswith("resumo cortado")
            or el.startswith("resumo curto")
            or "assuntos misturados" in el
            or el.startswith("só ")
        ):
            out.append(e)
    return out


def _manchetes(coleta):
    linhas = []
    for p in coleta.get("portais") or []:
        for m in p.get("manchetes") or []:
            if m.get("titulo") and m.get("link"):
                desc = notas.limpa_texto_fonte(m.get("descricao") or "")
                linhas.append({
                    "titulo": m["titulo"], "descricao": desc, "nome": p.get("nome"), "link": m["link"],
                    "tokens": notas.tokens(notas.texto_apoio(m["titulo"], desc)),
                })
    for tema, itens in (coleta.get("temas") or {}).items():
        for m in itens:
            if m.get("titulo") and m.get("link") and m.get("fonte"):
                desc = notas.limpa_texto_fonte(m.get("descricao") or "")
                linhas.append({
                    "titulo": m["titulo"], "descricao": desc, "nome": m["fonte"], "link": m["link"],
                    "tokens": notas.tokens(notas.texto_apoio(m["titulo"], desc)), "tema": tema,
                })
    return linhas


def _apoio(item, manchetes):
    nomes = {o.get("name") for o in item.get("outlets") or []}
    titulo = notas.tokens(item.get("title") or "")
    apoio = []
    for m in manchetes:
        if m["nome"] not in nomes:
            continue
        if any(o.get("url") == m["link"] for o in item.get("outlets") or []):
            apoio.append(m)
            continue
        if notas.mesmo_fato(titulo, m["tokens"], item.get("title") or "", m["titulo"]):
            apoio.append(m)
    return apoio


def _frase_apoiada(frase, apoio):
    tk = {w for w in notas.tokens(frase) if w not in notas.UBIQUOS and len(w) >= 4}
    if len(tk) < 2:
        tk = notas.tokens(frase)
    if not tk:
        return False
    for m in apoio:
        if len(tk & m["tokens"]) / len(tk) >= 0.7:
            return True
    return False


def conferir(edicao, coleta):
    manchetes = _manchetes(coleta)
    por_link = {(m["nome"], m["link"]) for m in manchetes}
    erros, avisos = [], []
    itens = edicao.get("items") or []
    if len(itens) < 8:
        erros.append(f"só {len(itens)} notícias")
    vistos = []
    for it in itens:
        titulo = it.get("title") or ""
        urls = " ".join(o.get("url") or "" for o in it.get("outlets") or [])
        resumo = it.get("summary") or ""
        if notas.eh_entretenimento(titulo, urls, resumo):
            erros.append(f"entretenimento publicado: {titulo}")
        elif it.get("segment") == "politica":
            # Sem sinal político, editoria de lifestyle não pode ficar em Política.
            texto = " ".join(x for x in (titulo, resumo) if x)
            if not any(notas.contem_termo(texto, k) for k in notas.SINAIS_POLITICA) and any(
                p in notas.norm(urls) for p in ("/ela/", "/play/novelas/", "/horoscopo")
            ):
                erros.append(f"seção politica para fato de lifestyle: {titulo}")
        if "—" in json.dumps(it, ensure_ascii=False) or "–" in json.dumps(it, ensure_ascii=False):
            erros.append(f"travessão: {titulo}")
        if "Manchete coletada automaticamente" in (it.get("summary") or ""):
            erros.append(f"resumo boilerplate: {titulo}")
        if resumo_cortado(resumo):
            erros.append(f"resumo cortado: {titulo}")
        if reparar.resumo_curto(it):
            erros.append(f"resumo curto: {titulo}")
        seg = notas.ajustar_segmento(titulo, it.get("segment") or "", "", urls, resumo)
        if seg and seg != it.get("segment") and seg in ("esportes", "economia", "internacional", "educacao", "ciencia", "rio", "seguranca", "politica"):
            erros.append(f"seção {it.get('segment')} para fato de {seg}: {titulo}")
        for o in it.get("outlets") or []:
            if (o.get("name"), o.get("url")) not in por_link:
                erros.append(f"link fora da coleta: {o.get('name')} em «{titulo}»")
        apoio = _apoio(it, manchetes)
        if not apoio:
            erros.append(f"nenhuma manchete da coleta apoia: {titulo}")
        frases = _frases(it.get("summary") or "")
        if not frases:
            erros.append(f"sem resumo: {titulo}")
        for fr in frases:
            if notas.frase_lixo(fr):
                erros.append(f"frase sem manchete de apoio: «{fr}» ({titulo})")
            elif apoio and not _frase_apoiada(fr, apoio):
                erros.append(f"frase sem manchete de apoio: «{fr}» ({titulo})")
            for n in re.findall(r"\d+", fr):
                if apoio and not any(n in _texto_manchete(m) for m in apoio):
                    erros.append(f"número {n} não está nas manchetes: «{fr}»")
        mistura = assuntos_misturados(it, apoio)
        if mistura:
            erros.append(mistura)
        for prev in vistos:
            if notas.mesmo_fato(notas.tokens(titulo), notas.tokens(prev), titulo, prev):
                erros.append(f"fato duplicado na edição: «{titulo}» e «{prev}»")
        vistos.append(titulo)
        for x in edicao.get("x_trends") or []:
            if x.get("item_id") == it.get("id"):
                sobre = x.get("sobre") or ""
                if sobre and not _frase_apoiada(sobre, apoio or [{"titulo": titulo, "tokens": notas.tokens(titulo)}]):
                    erros.append(f"trend «{x.get('nome')}» com sobre sem apoio na notícia")
                chaves = {w for w in notas.tokens(x.get("nome") or "") if len(w) >= 4}
                if chaves and not chaves <= notas.tokens(titulo):
                    erros.append(f"trend «{x.get('nome')}» não está no título da notícia ligada")
    nouts = [len(i.get("outlets") or []) for i in itens] or [0]
    media = round(sum(nouts) / len(nouts), 2)
    secoes = {}
    for i in itens:
        secoes[i.get("segment")] = secoes.get(i.get("segment"), 0) + 1
    return erros, avisos, {"noticias": len(itens), "media_portais": media, "secoes": secoes, "trends": len(edicao.get("x_trends") or []), "gerador": edicao.get("gerador"), "modelo": edicao.get("modelo")}


def _outlets_do_nucleo(item, apoio):
    """Mantém só os veículos cujo texto é o mesmo fato do título."""
    mistura = assuntos_misturados(item, apoio)
    if not mistura:
        return list(item.get("outlets") or []), apoio
    estranhas = set()
    # reextrai as manchetes estranhas pelo mesmo critério
    titulo = item.get("title") or ""
    base = notas.texto_apoio(titulo, "")
    base_tk = notas.tokens(base)
    for m in apoio:
        man = m.get("titulo") or ""
        fonte = _texto_manchete(m)
        inter = {w for w in (base_tk & notas.tokens(fonte)) if w not in notas.UBIQUOS and len(w) >= 4}
        if notas.mesmo_fato(base_tk, notas.tokens(fonte), base, fonte) or len(inter) >= 3 or (
            len(inter) >= 2 and not notas.conflito_numeros(base, fonte)
        ):
            continue
        estranhas.add((m.get("nome"), m.get("link")))
        estranhas.add(man)
    novos = []
    for o in item.get("outlets") or []:
        if (o.get("name"), o.get("url")) in estranhas:
            continue
        # se o url não bateu, ainda cai se o título da fonte for estranho
        if any((o.get("url") == m.get("link") and m.get("titulo") in estranhas) for m in apoio):
            continue
        novos.append(o)
    return novos, [m for m in apoio if (m.get("nome"), m.get("link")) not in estranhas]


def _frases_das_fontes(apoio, n=5):
    """Monta até n frases ancoradas só no material das fontes, sem invenção."""
    cands, vistos = [], []
    for m in apoio:
        txt = _texto_manchete(m)
        partes = _frases(txt) or ([txt] if len(txt) >= 40 else [])
        for fr in partes:
            fr = notas.limpa_texto_fonte(fr)
            if len(fr) < 40 or notas.frase_lixo(fr) or resumo_cortado(fr if fr.endswith(".") else fr + "."):
                continue
            if not _frase_apoiada(fr, apoio):
                continue
            tk = notas.tokens(fr)
            if any(len(tk & notas.tokens(v)) / max(1, len(tk)) >= 0.8 for v in vistos):
                continue
            cands.append(fr if fr.endswith((".", "!", "?")) else fr + ".")
            vistos.append(fr)
            if len(cands) >= n:
                return cands
    return cands


def reparar_itens_reprovados(edicao, coleta):
    """Regenera ou descarta só o item que o QA reprovaria, antes de barrar a edição.

    Não afrouxa mistura, truncamento nem frase sem apoio: tenta um resumo de
    5 frases ancoradas nas fontes do núcleo e joga fora o item irreparável.
    """
    manchetes = _manchetes(coleta)
    restantes, descartados = [], []
    for it in list(edicao.get("items") or []):
        titulo = it.get("title") or ""
        apoio = _apoio(it, manchetes)
        outlets, apoio = _outlets_do_nucleo(it, apoio)
        if not outlets or not apoio:
            descartados.append({"titulo": titulo, "motivo": "sem fonte do mesmo fato"})
            continue
        it["outlets"] = outlets
        urls = " ".join(o.get("url") or "" for o in outlets)
        seg = notas.ajustar_segmento(titulo, it.get("segment") or "", "", urls, it.get("summary") or "")
        if seg:
            it["segment"] = seg
        frases = []
        for fr in _frases(it.get("summary") or ""):
            fr = notas.limpa_texto_fonte(fr)
            if not fr or notas.frase_lixo(fr):
                continue
            if not _frase_apoiada(fr, apoio):
                continue
            frases.append(fr if fr.endswith((".", "!", "?")) else fr + ".")
        if frases and resumo_cortado(" ".join(frases)):
            frases = frases[:-1]
        def _grave(frases_agora):
            txt = " ".join(frases_agora).strip()
            if not txt:
                return True
            if resumo_cortado(txt if txt.endswith((".", "!", "?")) else txt + "."):
                return True
            if assuntos_misturados(it, apoio):
                return True
            return any(notas.frase_lixo(fr) or not _frase_apoiada(fr, apoio) for fr in frases_agora)

        if _grave(frases) or len(frases) != 5:
            novas = _frases_das_fontes(apoio, 5)
            if len(novas) == 5:
                frases = novas
            elif _grave(frases) and not _grave(novas) and len(novas) == 5:
                frases = novas
            elif not _grave(novas) and len(novas) == 5:
                frases = novas
            elif len(frases) != 5:
                descartados.append({"titulo": titulo, "motivo": "item irreparável (mistura, corte ou frase sem apoio)"})
                continue
        resumo = " ".join(frases).strip()
        if not resumo.endswith((".", "!", "?")):
            resumo += "."
        it["summary"] = resumo
        # Recheca com o mesmo apoio do conferir, para não deixar frase sem suporte.
        apoio_final = _apoio(it, manchetes)
        frases_finais = _frases(it.get("summary") or "")
        if (
            len(frases_finais) != 5
            or _grave(frases_finais)
            or any(not _frase_apoiada(fr, apoio_final) for fr in frases_finais)
        ):
            novas = _frases_das_fontes(apoio_final or apoio, 5)
            if len(novas) == 5 and not any(not _frase_apoiada(fr, apoio_final or apoio) for fr in novas):
                resumo = " ".join(novas).strip()
                if not resumo.endswith((".", "!", "?")):
                    resumo += "."
                it["summary"] = resumo
            else:
                descartados.append({"titulo": titulo, "motivo": "ainda reprovado após reparo"})
                continue
        if reparar.resumo_curto(it):
            novas = _frases_das_fontes(apoio, 5)
            if novas:
                resumo = " ".join(novas).strip()
                if not resumo.endswith((".", "!", "?")):
                    resumo += "."
                it["summary"] = resumo
            if reparar.resumo_curto(it) or _grave(_frases(it.get("summary") or "")):
                descartados.append({"titulo": titulo, "motivo": "resumo curto irreparável"})
                continue
        restantes.append(it)
    edicao["items"] = restantes
    # trends só com item ainda presente e nome amarrado ao texto
    if edicao.get("x_trends") is not None:
        import noticias_reparar as _rep
        edicao["x_trends"] = _rep.limpar_trends(edicao.get("x_trends") or [], restantes)
    return edicao, {"descartados": descartados, "restantes": len(restantes)}


def main():
    if "--self-test" in sys.argv:

        _self_test()
        return
    ed_path = Path(sys.argv[1] if len(sys.argv) > 1 else "")
    col_path = Path(sys.argv[2] if len(sys.argv) > 2 else "coleta-noticias.json")
    if not ed_path:
        from datetime import datetime, timedelta, timezone
        agora = datetime.now(timezone(timedelta(hours=-3)))
        h = "06h" if agora.hour < 9 else "12h" if agora.hour < 16 else "19h"
        ed_path = ROOT / "noticias" / f"{agora:%Y-%m-%d}-{h}.json"
    if not ed_path.is_absolute():
        ed_path = ROOT / ed_path
    if not col_path.is_absolute():
        col_path = ROOT / col_path
    cfg = json.loads((ROOT / "noticias" / "config.json").read_text(encoding="utf-8"))
    edicao = json.loads(ed_path.read_text(encoding="utf-8"))
    coleta = json.loads(col_path.read_text(encoding="utf-8")) if col_path.exists() else {"portais": []}
    edicao, rel = reparar.reparar(edicao, cfg, google=True)
    edicao, rel_itens = reparar_itens_reprovados(edicao, coleta)
    ed_path.write_text(json.dumps(edicao, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("reparo " + json.dumps(rel, ensure_ascii=False))
    print("reparo_itens " + json.dumps(rel_itens, ensure_ascii=False))
    for d in rel_itens.get("descartados") or []:
        print(f"item descartado: {d.get('titulo')} ({d.get('motivo')})")
    graves = list(reparar.conferir_graves(edicao, cfg))
    erros, avisos, metricas = conferir(edicao, coleta) if coleta.get("portais") is not None else ([], [], {})
    # link fora da coleta e número ausente da manchete continuam aviso:
    # depois do reparo o endereço já é o do portal, não o do Google da coleta.
    bloqueantes = erros_bloqueantes(erros)
    demais = [e for e in erros if e not in bloqueantes]
    avisos = list(avisos) + [e for e in demais if e not in graves]
    for e in bloqueantes:
        if e not in graves:
            graves.append(e)
    print(json.dumps(metricas, ensure_ascii=False))
    for a in avisos:
        print("AVISO " + a)
    for e in graves:
        print("ERRO " + e)
    print(f"qa: {len(graves)} erros, {len(avisos)} avisos")
    sys.exit(1 if graves else 0)


def _coleta_de_itens(*itens):
    portais = {}
    for it in itens:
        for o in it.get("outlets") or []:
            nome = o.get("name") or "?"
            portais.setdefault(nome, {"nome": nome, "manchetes": []})
            portais[nome]["manchetes"].append({
                "titulo": o.get("titulo") or "",
                "descricao": o.get("descricao") or "",
                "link": o.get("url") or "",
            })
    return {"portais": list(portais.values()), "temas": {}}


def _self_test():
    # Cortes reais da edição 25/11-06h.
    assert resumo_cortado("O motivo do portugu.")
    assert resumo_cortado(
        "No último jogo antes da final da Libertadores de 2026, o Palmeiras perdeu de 3 a 0. O motivo do portugu."
    )
    assert resumo_cortado("Em meio a isso, Rogério Ceni aparece como um.")
    assert resumo_cortado("GTA VI prende jogadores às telas e faz disp")
    assert not resumo_cortado("Davi Alcolumbre autorizou a Advocacia do Senado a atuar no STF.")
    assert not resumo_cortado("O Palmeiras perdeu de 3 a 0 para a Chapecoense.")
    assert not resumo_cortado("O Congresso vota o projeto de transparência número 1.")
    assert resumo_cortado("A autorização vale no colégio em que o filho de D.")
    assert resumo_cortado("O governo criticou a decisão norte-americ.")

    # Frase sem apoio: resumo fala de Petrobras, as manchetes são só do caso Alcolumbre/Van Hattem.
    item_apoio = {
        "id": "alcolumbre",
        "segment": "politica",
        "title": "Alcolumbre atua em defesa de Van Hattem e indica possibilidade de novo ajuste fiscal ainda em 2026",
        "summary": (
            "Davi Alcolumbre autorizou a Advocacia do Senado a atuar no STF em defesa da imunidade parlamentar de Marcel Van Hattem. "
            "A Petrobras mantém sua política de preços dos combustíveis, mas não descarta mudanças pontuais devido à volatilidade global."
        ),
        "outlets": [
            {
                "name": "Gazeta do Povo",
                "url": "https://www.gazetadopovo.com.br/vida-e-cidadania/alcolumbre-autoriza-advocacia-do-senado-a-atuar-em-defesa-de-marcel-van-hattem/",
                "titulo": "Alcolumbre autoriza Advocacia do Senado a atuar em defesa de Marcel Van Hattem",
            },
            {
                "name": "Revista Oeste",
                "url": "https://revistaoeste.com/no-ponto/alcolumbre-autoriza-advocacia-do-senado-a-atuar-no-caso-van-hattem/",
                "titulo": "Alcolumbre autoriza advocacia do Senado a atuar no caso Van Hattem",
            },
        ],
    }
    # Completa a edição com itens neutros para passar do piso de 8 notícias.
    extras = []
    for i in range(7):
        extras.append({
            "id": f"extra-{i}",
            "segment": "politica",
            "title": f"Congresso vota o projeto de transparência número {i+1}",
            "summary": (
                f"O Congresso vota o projeto de transparência número {i+1}. "
                f"A votação do projeto de transparência número {i+1} ocorre nesta sessão do Congresso. "
                f"O texto da transparência número {i+1} segue para o plenário. "
                f"Líderes do Congresso articulam o acordo da transparência número {i+1}. "
                f"A sessão do Congresso deve concluir a transparência número {i+1} ainda hoje."
            ),
            "outlets": [{
                "name": "Poder360",
                "url": f"https://www.poder360.com.br/poder-congresso/projeto-transparencia-{i+1}/",
                "titulo": f"Congresso vota o projeto de transparência número {i+1}",
                "descricao": (
                    f"A votação do projeto de transparência número {i+1} ocorre nesta sessão do Congresso. "
                    f"O texto da transparência número {i+1} segue para o plenário. "
                    f"Líderes do Congresso articulam o acordo da transparência número {i+1}. "
                    f"A sessão do Congresso deve concluir a transparência número {i+1} ainda hoje."
                ),
            }],
        })

    erros, avisos, _ = conferir({"items": [item_apoio] + extras}, _coleta_de_itens(item_apoio, *extras))
    assert any("frase sem manchete de apoio" in e and "Petrobras" in e for e in erros), erros
    assert erros_bloqueantes(erros)
    assert not any("frase sem manchete de apoio" in a for a in avisos)

    # Cluster misturado da edição 25/11-06h: Alcolumbre + Petrobras no mesmo item.
    item_mistura = {
        "id": "alcolumbre-petrobras",
        "segment": "politica",
        "title": "Alcolumbre atua em defesa de Van Hattem e indica possibilidade de novo ajuste fiscal ainda em 2026",
        "summary": (
            "Davi Alcolumbre autorizou a Advocacia do Senado a atuar no STF em defesa da imunidade parlamentar de Marcel Van Hattem. "
            "A Petrobras mantém sua política de preços dos combustíveis, mas não descarta mudanças pontuais."
        ),
        "outlets": [
            {
                "name": "Gazeta do Povo",
                "url": "https://www.gazetadopovo.com.br/vida-e-cidadania/alcolumbre-autoriza-advocacia-do-senado-a-atuar-em-defesa-de-marcel-van-hattem/",
                "titulo": "Alcolumbre autoriza Advocacia do Senado a atuar em defesa de Marcel Van Hattem",
            },
            {
                "name": "Valor Econômico",
                "url": "https://valor.globo.com/empresas/noticia/2026/11/24/se-houver-mudancas-na-politica-de-precos-dos-combustiveis-sera-pontual-e-conjuntural-diz-petrobras.ghtml",
                "titulo": "Se houver mudanças na política de preços dos combustíveis, será pontual e conjuntural, diz Petrobras",
            },
        ],
    }
    erros2, avisos2, _ = conferir({"items": [item_mistura] + extras}, _coleta_de_itens(item_mistura, *extras))
    assert any("assuntos misturados" in e and "combust" in e.lower() for e in erros2), erros2
    assert any("assuntos misturados" in e for e in erros_bloqueantes(erros2))
    assert not any("assuntos misturados" in a for a in avisos2)

    # Outro cluster real: segurança + novela + Anatel no mesmo item.
    item_crime = {
        "id": "violencia",
        "segment": "seguranca",
        "title": "Diversos casos de segurança pública e violência são registrados no Brasil",
        "summary": (
            "Pedro virou suspeito da morte de Arthur Brandão em novela da TV Globo. "
            "Ex-boxeador foi denunciado por homicídio triplamente qualificado em Santa Catarina."
        ),
        "outlets": [
            {
                "name": "Estadão",
                "url": "https://www.estadao.com.br/brasil/mp-denuncia-ex-boxeador-suspeito-espancar-homem-morte-homicidio-triplamente-qualificado-npr/",
                "titulo": "MP denuncia ex-boxeador suspeito de espancar homem até a morte",
            },
            {
                "name": "O Globo",
                "url": "https://oglobo.globo.com/cultura/televisao/noticia/2026/11/25/quem-ama-cuida-pedro-vira-suspeito-da-morte-de-arthur-brandao.ghtml",
                "titulo": "Quem ama cuida: Pedro vira suspeito da morte de Arthur Brandão",
            },
        ],
    }
    erros3, _, _ = conferir({"items": [item_crime] + extras}, _coleta_de_itens(item_crime, *extras))
    assert any("assuntos misturados" in e for e in erros3), erros3

    # Governo + Sabrina Sato, outro cluster da mesma edição.
    item_artistas = {
        "id": "gastos-artistas",
        "segment": "economia",
        "title": "Governo reduz contenção de gastos e artistas anunciam eventos para 2027",
        "summary": (
            "Após aprovação do projeto de ajuste fiscal, governo diminuiu contenção de gastos no Orçamento de 2026 para R$ 12,6 bilhões. "
            "Apresentadora Sabrina Sato revelou previsão para nascimento do filho no Rio."
        ),
        "outlets": [
            {
                "name": "Estadão",
                "url": "https://www.estadao.com.br/economia/contencao-de-gastos-no-orcamento-cai-de-r-161-bi-para-r-126-bi-apos-aprovacao-de-pacote-fiscal/",
                "titulo": "Contenção de gastos no Orçamento cai de R$ 16,1 bi para R$ 12,6 bi após aprovação de pacote fiscal",
            },
            {
                "name": "O Dia",
                "url": "https://odia.ig.com.br/diversao/celebridades/2026/11/7325662-sabrina-sato-revela-previsao-para-nascimento-do-filho-rio.html",
                "titulo": "Sabrina Sato revela previsão para nascimento do filho no Rio",
            },
        ],
    }
    erros4, _, _ = conferir({"items": [item_artistas] + extras}, _coleta_de_itens(item_artistas, *extras))
    assert any("assuntos misturados" in e and "Sabrina" in e for e in erros4), erros4

    # Resumo cortado vira erro bloqueante.
    item_corte = {
        "id": "chapeco",
        "segment": "esportes",
        "title": "Abel compara Palmeiras à Chapecoense e cita drama do Inter no Brasileirão para rebater críticas",
        "summary": (
            "No último jogo antes da final da Libertadores de 2026, o Palmeiras perdeu de 3 a 0 para a Chapecoense. "
            "O motivo do portugu."
        ),
        "outlets": [{
            "name": "CNN Brasil",
            "url": "https://www.cnnbrasil.com.br/esportes/brasileirao/time-c-do-palmeiras-perde-para-a-chapecoense-no-brasileirao/",
            "titulo": "Time C do Palmeiras perde para a Chapecoense no Brasileirão",
        }],
    }
    erros5, avisos5, _ = conferir({"items": [item_corte] + extras}, _coleta_de_itens(item_corte, *extras))
    assert any(e.startswith("resumo cortado:") and "Chapecoense" in e for e in erros5), erros5
    assert any(e.startswith("resumo cortado:") for e in erros_bloqueantes(erros5))
    # Número fora da manchete continua no saco de erros brutos, mas não é bloqueante.
    assert all(not e.lower().startswith("número ") or e not in erros_bloqueantes(erros5) for e in erros5)

    # Run 45747320731: frase apoiada na descrição, não no título, não é invenção.
    item_desc = {
        "id": "trabuco",
        "segment": "politica",
        "title": "Lula sondou Luiz Trabuco, do Bradesco, para o Ministério da Fazenda",
        "summary": (
            "O presidente Luiz Inácio Lula da Silva sondou Luiz Carlos Trabuco Cappi, presidente do Conselho de Administração do Bradesco, para assumir o Ministério da Fazenda. "
            "O encontro ocorreu fora da agenda oficial no Palácio da Alvorada."
        ),
        "outlets": [{
            "name": "Folha de S.Paulo",
            "url": "https://www1.folha.uol.com.br/trabuco",
            "titulo": "Lula sondou Luiz Trabuco, do Bradesco, para o Ministério da Fazenda",
            "descricao": (
                "Horas depois de se espalhar em Brasília a informação de que o presidente Luiz Inácio Lula da Silva se reuniu na segunda-feira com Luiz Carlos Trabuco Cappi, "
                "presidente do Conselho de Administração do Bradesco, para assumir o Ministério da Fazenda. O encontro ocorreu fora da agenda oficial no Palácio da Alvorada."
            ),
        }],
    }
    erros_d, _, _ = conferir({"items": [item_desc] + extras}, _coleta_de_itens(item_desc, *extras))
    assert not any("frase sem manchete de apoio" in e for e in erros_d), erros_d

    # Boilerplate copiado do lead continua erro, mesmo estando na descrição.
    item_lixo = dict(item_desc)
    item_lixo = {
        **item_desc,
        "id": "leia-mais",
        "summary": item_desc["summary"] + " Leia mais (11/25/2026 - 22h09).",
    }
    erros_l, _, _ = conferir({"items": [item_lixo] + extras}, _coleta_de_itens(item_lixo, *extras))
    assert any("frase sem manchete de apoio" in e and "Leia mais" in e for e in erros_l), erros_l

    # Ângulo diferente do mesmo fato (título curto vs lead com Trabuco) não é mistura.
    item_angulo = {
        "id": "trabuco-angulo",
        "segment": "politica",
        "title": "Lula sondou Luiz Trabuco, do Bradesco, para o Ministério da Fazenda",
        "summary": "O presidente Luiz Inácio Lula da Silva sondou Luiz Carlos Trabuco Cappi para assumir o Ministério da Fazenda.",
        "outlets": [
            {
                "name": "Folha de S.Paulo",
                "url": "https://www1.folha.uol.com.br/trabuco-1",
                "titulo": "Lula sondou Luiz Trabuco, do Bradesco, para o Ministério da Fazenda",
                "descricao": "Lula sondou Luiz Carlos Trabuco Cappi, do Bradesco, para o Ministério da Fazenda.",
            },
            {
                "name": "Estadão",
                "url": "https://www.estadao.com.br/trabuco-dolar",
                "titulo": "Após especulação mexer com dólar, Lula teria sondado executivo do Bradesco",
                "descricao": "Lula teria sondado Luiz Trabuco, presidente do Conselho do Bradesco, para o Ministério da Fazenda, depois de o dólar reagir.",
            },
        ],
    }
    erros_a, _, _ = conferir({"items": [item_angulo] + extras}, _coleta_de_itens(item_angulo, *extras))
    assert not any("assuntos misturados" in e for e in erros_a), erros_a

    # Mistura real do mesmo run: tarifaço + qualidade do ar em Manaus.
    item_manaus = {
        "id": "tarifaco",
        "segment": "internacional",
        "title": "Brasil critica possível novo tarifaço dos EUA e diz que não cederá",
        "summary": "O governo brasileiro criticou a decisão dos Estados Unidos de propor novas tarifas de importação.",
        "outlets": [
            {
                "name": "G1",
                "url": "https://g1.globo.com/tarifaco",
                "titulo": "Governo Lula diz que não cederá a ameaças após tarifas dos EUA",
                "descricao": "O governo brasileiro classificou como inaceitável o novo tarifaço proposto pelos Estados Unidos.",
            },
            {
                "name": "G1",
                "url": "https://g1.globo.com/manaus-ar",
                "titulo": "Manaus segue com qualidade do ar muito ruim nesta quarta-feira",
                "descricao": "A qualidade do ar em Manaus permanece em nível muito ruim nesta quarta-feira por causa das queimadas.",
            },
        ],
    }
    erros_m, _, _ = conferir({"items": [item_manaus] + extras}, _coleta_de_itens(item_manaus, *extras))
    assert any("assuntos misturados" in e and "Manaus" in e for e in erros_m), erros_m

    # Reparo descarta o veículo misturado e reconstitui 5 frases ancoradas; a edição segue.
    fontes5 = []
    descricoes = [
        "O governo brasileiro criticou a decisão dos Estados Unidos de propor novas tarifas de importação sobre produtos brasileiros.",
        "A medida foi anunciada pelo Escritório do Representante Comercial dos Estados Unidos e inclui açúcar e aeronaves.",
        "Em nota, a Secom afirmou que o país não cederá a pressões comerciais de Washington.",
        "O Itamaraty avaliou o anúncio como uma tentativa de forçar mudanças no Pix e em regras digitais.",
        "A indústria aeronáutica acompanha o impacto da tarifa sobre as exportações da Embraer.",
    ]
    for i, d in enumerate(descricoes):
        fontes5.append({
            "name": f"Portal {i}",
            "url": f"https://exemplo.com/tarifa-{i}",
            "titulo": "EUA propõem novas tarifas sobre o Brasil",
            "descricao": d,
        })
    item_reparo = {
        "id": "tarifas-reparo",
        "segment": "internacional",
        "title": "EUA propõem novas tarifas sobre o Brasil e pressionam por mudanças no Pix",
        "summary": "O governo dos Estados Unidos propôs novas tarifas. Leia mais (11/25/2026 - 22h09).",
        "outlets": fontes5 + [{
            "name": "G1",
            "url": "https://g1.globo.com/manaus-ar-2",
            "titulo": "Manaus segue com qualidade do ar muito ruim nesta quarta-feira",
            "descricao": "A qualidade do ar em Manaus permanece em nível muito ruim nesta quarta-feira por causa das queimadas.",
        }],
    }
    ed_rep, rel_rep = reparar_itens_reprovados({"items": [item_reparo] + extras}, _coleta_de_itens(item_reparo, *extras))
    assert rel_rep["restantes"] >= 8, rel_rep
    consertado = next(i for i in ed_rep["items"] if i["id"] == "tarifas-reparo")
    assert "Manaus" not in (consertado.get("summary") or "")
    assert "Leia mais" not in (consertado.get("summary") or "")
    assert len(_frases(consertado["summary"])) == 5
    erros_r, _, _ = conferir(ed_rep, _coleta_de_itens(*ed_rep["items"]))
    assert not any("assuntos misturados" in e and "tarifas-reparo" in e or (consertado["title"] in e and "assuntos misturados" in e) for e in erros_r), erros_r
    assert not any("frase sem manchete de apoio" in e and consertado["title"] in e for e in erros_r), erros_r

    # Resumo curto irreparável é descartado; o restante da edição segue.
    curto = {
        "id": "ciencia-delas",
        "segment": "ciencia",
        "title": "Com tema Ciência Delas, Semana de Ciência e Tecnologia vai até sábado",
        "summary": "Com tema Ciência Delas, Semana de Ciência e Tecnologia vai até sábado Meia Hora.",
        "outlets": [{
            "name": "Meia Hora",
            "url": "https://www.meiahora.com.br/ciencia-delas",
            "titulo": "Com tema Ciência Delas, Semana de Ciência e Tecnologia vai até sábado",
        }],
    }
    ed_c, rel_c = reparar_itens_reprovados({"items": [curto] + extras}, _coleta_de_itens(curto, *extras))
    assert any(d.get("titulo", "").startswith("Com tema") for d in rel_c["descartados"]), rel_c
    assert rel_c["restantes"] >= 7, rel_c
    assert all(i["id"] != "ciencia-delas" for i in ed_c["items"])

    # Run 45753339671: ângulo do réu no mesmo feminicídio (idade 22 vs pena 28) não é mistura.
    item_bh = {
        "id": "bh-noiva",
        "segment": "seguranca",
        "title": "Homem é condenado a 28 anos de prisão por matar ex-noiva a facadas em BH",
        "summary": "Wallef César Oliveira Gonçalves foi condenado a 28 anos e um mês de prisão em regime fechado por matar a ex-noiva, Vitória Alves Silva, de 22 anos, em agosto de 2024.",
        "outlets": [
            {
                "name": "Estado de Minas",
                "url": "https://www.em.com.br/gerais/2026/11/7544457-homem-e-condenado-a-28-anos-de-prisao-por-matar-ex-noiva-a-facadas.html",
                "titulo": "Homem é condenado a 28 anos de prisão por matar ex-noiva a facadas",
                "descricao": "Wallef César Oliveira Gonçalves foi condenado a 28 anos e um mês de prisão em regime fechado por matar a ex-noiva, Vitória Alves Silva, de 22 anos.",
            },
            {
                "name": "Estado de Minas",
                "url": "https://www.em.com.br/gerais/2026/11/preferia-morrer",
                "titulo": "‘Preferia morrer a voltar com ele’: réu relata discussão antes de matar ex-noiva na Antônio Carlos, em BH",
                "descricao": "O julgamento de Wallef César Oliveira Gonçalves, acusado de matar a facadas a ex-noiva, Vitória Alves Silva, de 22 anos, em agosto de 2024, começou nesta quarta-feira (25).",
            },
        ],
    }
    erros_bh, _, _ = conferir({"items": [item_bh] + extras}, _coleta_de_itens(item_bh, *extras))
    assert not any("assuntos misturados" in e and "28 anos" in e for e in erros_bh), erros_bh

    # 1. resumo de 2 frases é erro bloqueante; o reparo descarta se não dá para completar 5
    curto_ibge = {
        "id": "ibge-curto",
        "segment": "educacao",
        "title": "IBGE prorroga inscrições para concurso de recenseadores agropecuários",
        "summary": "O IBGE prorrogou as inscrições para concurso de recenseadores agropecuários. A Bahia é o estado que oferece mais oportunidades.",
        "outlets": [{
            "name": "Agência Brasil",
            "url": "https://agenciabrasil.ebc.com.br/ibge-recenseadores",
            "titulo": "IBGE prorroga inscrições para concurso de recenseadores agropecuários",
            "descricao": "O IBGE prorrogou as inscrições. A Bahia oferece mais vagas.",
        }],
    }
    erros_ib, _, _ = conferir({"items": [curto_ibge] + extras}, _coleta_de_itens(curto_ibge, *extras))
    assert any(e.startswith("resumo curto:") and "IBGE" in e for e in erros_bloqueantes(erros_ib)), erros_ib
    ed_ib, rel_ib = reparar_itens_reprovados({"items": [curto_ibge] + extras}, _coleta_de_itens(curto_ibge, *extras))
    assert all(i["id"] != "ibge-curto" for i in ed_ib["items"])

    # 2. lixo de YouTube no resumo é bloqueante; título perde hashtag no reparo de notas
    assert "JR24H" not in notas.limpa_manchete("Temporais no PR atingem 50 cidades. #JR24H #Shorts")

    # 4. seção errada: STF em economia e escolas em economia
    item_stf = {
        "id": "stf-turno",
        "segment": "economia",
        "title": "STF marca para 4 de dezembro o início do julgamento sobre segundo turno no Rio de Janeiro",
        "summary": (
            "O STF marcou para 4 de dezembro o julgamento sobre o segundo turno no Rio de Janeiro. "
            "A sessão será virtual. A votação segue até o dia 15. O ministro relator conduz o caso. "
            "O pedido foi apresentado pelo PSD."
        ),
        "outlets": [{
            "name": "G1",
            "url": "https://g1.globo.com/stf-segundo-turno",
            "titulo": "STF marca para 4 de dezembro o início do julgamento sobre segundo turno no Rio de Janeiro",
            "descricao": "O STF julga o pedido de segundo turno no Rio de Janeiro em sessão virtual até 15 de dezembro.",
        }],
    }
    erros_stf, _, _ = conferir({"items": [item_stf] + extras}, _coleta_de_itens(item_stf, *extras))
    assert any("seção economia para fato de politica" in e for e in erros_stf), erros_stf
    item_esc = {
        "id": "racismo-escolas",
        "segment": "economia",
        "title": "Câmara aprova protocolo nacional para combater racismo nas escolas",
        "summary": (
            "A Câmara aprovou o protocolo nacional contra o racismo nas escolas. "
            "O texto prevê acolhimento às vítimas. A medida vale para redes públicas e privadas. "
            "A Comissão de Educação encaminhou o projeto. O protocolo entra em vigor após a sanção."
        ),
        "outlets": [{
            "name": "Agência Câmara",
            "url": "https://www.camara.leg.br/racismo-escolas",
            "titulo": "Câmara aprova protocolo nacional para combater racismo nas escolas",
            "descricao": "A Câmara aprovou o protocolo nacional de acolhimento às vítimas de racismo nas escolas.",
        }],
    }
    erros_esc, _, _ = conferir({"items": [item_esc] + extras}, _coleta_de_itens(item_esc, *extras))
    assert any("seção economia para fato de educacao" in e for e in erros_esc), erros_esc

    # Frases do run 45819479692 (fintech) sem apoio: repara ou descarta, não barra a edição.
    item_ft = {
        "id": "fintech-fictor",
        "segment": "seguranca",
        "title": "Polícia prende donos de fintechs suspeitos de lavar dinheiro de golpes, sequestros e apostas ilegais",
        "summary": (
            "Polícia Civil e Ministério Público de São Paulo realizam operação contra estrutura financeira suspeita de lavagem de dinheiro. "
            "A ação investiga três instituições de pagamento. "
            "Duas fintechs foram desligadas do sistema Pix por determinação judicial. "
            "As instituições são suspeitas de movimentar recursos de crimes e apostas ilegais. "
            "Polícia prende donos de fintechs suspeitos de lavar dinheiro de crimes e apostas ilegais."
        ),
        "outlets": [{
            "name": "G1",
            "url": "https://g1.globo.com/sp/fintechs",
            "titulo": "Polícia prende donos de fintechs suspeitos de movimentar dinheiro de golpes, sequestros e apostas ilegais",
            "descricao": "Por determinação judicial, duas das instituições investigadas foram desligadas do sistema Pix.",
        }],
    }
    ed_ft, rel_ft = reparar_itens_reprovados({"items": [item_ft] + extras}, _coleta_de_itens(item_ft, *extras))
    ft = next((i for i in ed_ft["items"] if i["id"] == "fintech-fictor"), None)
    if ft:
        erros_ft, _, _ = conferir({"items": [ft] + extras}, _coleta_de_itens(ft, *extras))
        assert not any("frase sem manchete de apoio" in e and "fintechs" in e for e in erros_bloqueantes(erros_ft)), erros_ft
        assert not reparar.resumo_curto(ft)
    else:
        assert rel_ft["descartados"]

    print("qa_edicao: ok")


if __name__ == "__main__":
    main()

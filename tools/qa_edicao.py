"""Conferência editorial de uma edição contra a coleta que a gerou.

Cada frase do resumo precisa estar apoiada numa manchete do mesmo grupo.
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
    return False


def assuntos_misturados(item, apoio):
    """Erro quando qualquer manchete de apoio não é o mesmo fato do título.

    O detector antigo ignorava o link que já estava em outlets, então o
    cluster misturado da edição 25/11-06h não disparava.
    """
    titulo = item.get("title") or ""
    if not titulo or not apoio:
        return None
    estranhas = []
    vistos = set()
    for m in apoio:
        man = m.get("titulo") or ""
        if not man or man in vistos:
            continue
        if notas.mesmo_fato(notas.tokens(titulo), m["tokens"], titulo, man):
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
            or "assuntos misturados" in el
        ):
            out.append(e)
    return out


def _manchetes(coleta):
    linhas = []
    for p in coleta.get("portais") or []:
        for m in p.get("manchetes") or []:
            if m.get("titulo") and m.get("link"):
                linhas.append({"titulo": m["titulo"], "nome": p.get("nome"), "link": m["link"], "tokens": notas.tokens(m["titulo"])})
    for tema, itens in (coleta.get("temas") or {}).items():
        for m in itens:
            if m.get("titulo") and m.get("link") and m.get("fonte"):
                linhas.append({"titulo": m["titulo"], "nome": m["fonte"], "link": m["link"], "tokens": notas.tokens(m["titulo"]), "tema": tema})
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
        seg = notas.ajustar_segmento(titulo, it.get("segment") or "", "")
        if seg and seg != it.get("segment") and seg in ("esportes", "economia", "internacional", "educacao", "ciencia", "rio", "seguranca"):
            if it.get("segment") == "politica":
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
            if apoio and not _frase_apoiada(fr, apoio):
                erros.append(f"frase sem manchete de apoio: «{fr}» ({titulo})")
            for n in re.findall(r"\d+", fr):
                if apoio and not any(n in (m["titulo"] or "") for m in apoio):
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
    ed_path.write_text(json.dumps(edicao, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("reparo " + json.dumps(rel, ensure_ascii=False))
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
            portais[nome]["manchetes"].append({"titulo": o.get("titulo") or "", "link": o.get("url") or ""})
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
            "summary": f"O Congresso vota o projeto de transparência número {i+1}. A sessão segue nesta terça.",
            "outlets": [{
                "name": "Poder360",
                "url": f"https://www.poder360.com.br/poder-congresso/projeto-transparencia-{i+1}/",
                "titulo": f"Congresso vota o projeto de transparência número {i+1}",
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

    print("qa_edicao: ok")


if __name__ == "__main__":
    main()

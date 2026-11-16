"""Conferência editorial de uma edição contra a coleta que a gerou.

Cada frase do resumo precisa estar apoiada numa manchete do mesmo grupo.
O grupo não pode misturar fatos sem palavras em comum, o link tem de ser o
da coleta e a seção não pode ser a padrão errada (celebridade em Política).

Uso: python3 tools/qa_edicao.py noticias/AAAA-MM-DD-HHh.json coleta-noticias.json
Sai com código 1 se houver erro grave. O relatório vai para a saída padrão.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import noticias_notas as notas

ROOT = Path(__file__).resolve().parent.parent


def _frases(texto):
    return [p.strip() for p in __import__("re").split(r"[.!?]+", texto or "") if p.strip()]


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
        if notas.eh_entretenimento(titulo):
            erros.append(f"entretenimento publicado: {titulo}")
        if "—" in json.dumps(it, ensure_ascii=False) or "–" in json.dumps(it, ensure_ascii=False):
            erros.append(f"travessão: {titulo}")
        if "Manchete coletada automaticamente" in (it.get("summary") or ""):
            erros.append(f"resumo boilerplate: {titulo}")
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
            for n in __import__("re").findall(r"\d+", fr):
                if apoio and not any(n in (m["titulo"] or "") for m in apoio):
                    erros.append(f"número {n} não está nas manchetes: «{fr}»")
        # mistura: manchetes do apoio que não compartilham fato com o título
        misturadas = [m for m in apoio if not notas.mesmo_fato(notas.tokens(titulo), m["tokens"], titulo, m["titulo"]) and m["link"] not in {o.get("url") for o in it.get("outlets") or []}]
        if len(misturadas) >= 2:
            avisos.append(f"possível mistura em «{titulo}»: " + " | ".join(m["titulo"][:70] for m in misturadas[:3]))
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
    edicao = json.loads(ed_path.read_text(encoding="utf-8"))
    coleta = json.loads(col_path.read_text(encoding="utf-8"))
    erros, avisos, metricas = conferir(edicao, coleta)
    print(json.dumps(metricas, ensure_ascii=False))
    for a in avisos:
        print("AVISO " + a)
    for e in erros:
        print("ERRO " + e)
    print(f"qa: {len(erros)} erros, {len(avisos)} avisos")
    sys.exit(1 if erros else 0)


if __name__ == "__main__":
    main()

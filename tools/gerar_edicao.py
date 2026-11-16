"""Publica uma edição por regras quando a redação com IA não roda.
Uso: python3 tools/gerar_edicao.py coleta-noticias.json
Não cria fatos nem números além dos títulos. O modo fica em gerador=regras.
"""
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import noticias_notas as notas

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "noticias/config.json"
INDEX = ROOT / "noticias/index.json"
BRT = timezone(timedelta(hours=-3))


def resumo_de_itens(items):
    """Até 5 frases. Cada frase é uma manchete limpa que acrescenta fato ainda não dito.
    A primeira sai, de preferência, de um veículo da linha editorial."""
    def chave(e):
        linha = 0 if e["name"] in notas.LINHA else 1
        return (linha, -len(notas.tokens(e["title"]) - notas.UBIQUOS))

    frases, cobertos = [], set()
    for e in sorted(items, key=chave):
        t = notas.limpa_manchete(e["title"])
        if len(t) < 25 or t.strip(" .").isdigit():
            continue
        tk = notas.tokens(t)
        novos = (tk - notas.UBIQUOS) - cobertos
        if frases and len(novos) < 2:
            continue
        frases.append(t)
        cobertos |= tk
        if len(frases) == 5:
            break
    if not frases:
        return ""
    return ". ".join(frases) + "."


def agrupar(entries):
    """Funde o mesmo fato mesmo quando o título curto não cobre o título longo.

    Uma palavra rara (contrarregra, Dino, Sani) que quase não aparece na coleta
    basta para juntar. Dois nomes raros também. Número grande discordante separa.
    """
    from collections import Counter
    df = Counter()
    preparados = []
    for e in entries:
        ts = notas.tokens(e["title"])
        if len(ts) < 2 or notas.eh_entretenimento(e["title"]):
            continue
        preparados.append((e, ts))
        df.update(ts)

        genericas = set("""interesse eleicoes eleicao encontro horas depois antes dias modelo extrema
        direita esquerda fiscal pacote ajuste mercado aposta critica apoio plano proposta nacional
        brasileiro brasileiros cidade morte mortos preso presos policia operacao video detalhes saiba
        confira entenda veja onde como apos durante contra sobre entre desde hoje ontem agora nova novo
        diz afirma segundo aponta revela mostra faz vai ter ser pode devem meio lista paises pessoas
        pessoa caso grande manha noite ano anos mil reais video primeira terceiro quarta
        novembro dezembro janeiro marco abril maio junho julho agosto setembro outubro sinaliza
        influencia jovens jovem mulher homem bairro escola compra compras processo processos termina
        comeca acordo morre abre volta deixa podem fazer maior dois cinco duas sera tempo casos
        desde data risco crise meio prazo lucro dizem prova saude falha porto civil corpo papa
        podem podem apos apos""".split())

    def chaves(ts):
        out = set()
        for w in ts:
            if w in notas.UBIQUOS or w in genericas or df[w] <= 1 or df[w] > 20:
                continue
            # Nome curto (Dino, Sani) só entra se for raro. Palavra longa pode repetir mais.
            if len(w) >= 6 or (len(w) >= 4 and df[w] <= 12):
                out.add(w)
        return out

    def junta(ta, tb, texto_a, texto_b):
        if notas.conflito_numeros(texto_a, texto_b):
            return False
        ra, rb = ta & notas.RESULTADO, tb & notas.RESULTADO
        if ra and rb and ra.isdisjoint(rb):
            return False
        if notas.mesmo_fato(ta, tb, texto_a, texto_b):
            return True
        comuns = chaves(ta) & chaves(tb)
        return len(comuns) >= 2

    preparados.sort(key=lambda et: -len(chaves(et[1])))
    groups = []
    for entry, ts in preparados:
        best, best_score = None, 0
        for g in groups:
            if junta(ts, g["terms"], entry["title"], g["anchor"]):
                score = len(chaves(ts) & chaves(g["terms"])) + len(ts & g["terms"]) / 10
                if score > best_score:
                    best, best_score = g, score
        if best is None:
            groups.append(dict(items=[entry], terms=set(ts), hint=entry["hint"], anchor=entry["title"]))
        else:
            best["items"].append(entry)

    def nucleo(g):
        c = Counter()
        for it in g["items"]:
            c.update(chaves(notas.tokens(it["title"])))
        return {w for w, n in c.items() if n >= 2 or len(g["items"]) == 1}

    for _ in range(5):
        saida, mudou = [], False
        for g in groups:
            ng = nucleo(g)
            achou = None
            for h in saida:
                if notas.conflito_numeros(g["anchor"], h["anchor"]):
                    continue
                if len(ng & nucleo(h)) >= 2 and notas.mesmo_fato(g["terms"], h["terms"], g["anchor"], h["anchor"]):
                    achou = h
                    break
            if achou is None:
                saida.append(g)
            else:
                achou["items"].extend(g["items"])
                mudou = True
        groups = saida
        if not mudou:
            break
    return groups


def segmento_grupo(items):
    votos = [notas.segmento_por_titulo(e["title"], e.get("hint") or "") for e in items]
    if "rio" in votos:
        return "rio"
    especificos = [v for v in votos if v != "politica"]
    if especificos:
        from collections import Counter
        return Counter(especificos).most_common(1)[0][0]
    return "politica"


def montar_trends(selected):
    path = ROOT / "noticias/trends.json"
    if not path.exists():
        return []
    try:
        trends = json.loads(path.read_text(encoding="utf-8")).get("trends") or []
    except (OSError, ValueError):
        return []
    ordem = {"politica": 0, "economia": 1, "seguranca": 2, "rio": 3, "educacao": 4, "internacional": 5, "ciencia": 6, "esportes": 7}
    achados = []
    for t in trends:
        nome = (t.get("nome") or "").strip()
        if not nome or notas.eh_entretenimento(nome):
            continue
        nt = notas.tokens(nome)
        if not nt:
            continue
        melhor, score = None, 0
        for it in selected:
            if it.get("segment") == "esportes":
                continue
            base = notas.tokens(it["title"])
            chaves = {w for w in nt if len(w) >= 4}
            # O nome inteiro do trend precisa estar no título. "Daniel" sozinho não pode
            # ligar Daniel Craig a Daniel Silveira.
            if not chaves or not chaves <= base:
                continue
            sc = len(chaves) / len(chaves)
            if sc > score:
                melhor, score = it, sc
        if melhor:
            achados.append((ordem.get(melhor["segment"], 9), -score, nome, t, melhor))
    saida = []
    for _, _, nome, t, it in sorted(achados)[:8]:
        fonte = {"name": it["outlets"][0]["name"], "url": it["outlets"][0]["url"]} if it.get("outlets") else None
        saida.append({
            "nome": nome,
            "volume": t.get("volume"),
            "url": t.get("url"),
            "sobre": it["title"].rstrip(".") + ".",
            "leitura": "neutro",
            "uso": "Explicar o fato com o que as fontes da edição já publicaram, sem acrescentar dado que não esteja nelas.",
            "item_id": it["id"],
            "fonte": fonte,
        })
    return saida


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "coleta-noticias.json")
    if not src.is_absolute():
        src = ROOT / src
    data = json.loads(src.read_text(encoding="utf-8"))
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    agora = datetime.now(BRT)
    edition = notas.id_edicao(agora)
    entries = []
    for p in data.get("portais", []):
        for m in p.get("manchetes", []):
            if m.get("titulo") and m.get("link") and not notas.eh_entretenimento(m["titulo"]):
                entries.append(dict(title=m["titulo"].strip(), name=p["nome"], camp=p.get("camp", ""), url=m["link"], hint="", when=m.get("quando", "")))
    for key, matches in data.get("temas", {}).items():
        for m in matches:
            if m.get("titulo") and m.get("link") and m.get("fonte") and not notas.eh_entretenimento(m["titulo"]):
                entries.append(dict(title=m["titulo"].strip(), name=m["fonte"], camp="", url=m["link"], hint=key, when=m.get("quando", "")))
    if len(entries) < 25:
        raise SystemExit("Coleta insuficiente: não publicar edição vazia ou incompleta")
    camps = {o["name"]: o.get("camp") for o in cfg["outlets"]}
    tiers = {o["name"]: o.get("tier") or 3 for o in cfg["outlets"]}
    for e in entries:
        if not e["camp"]:
            e["camp"] = camps.get(e["name"], "")
    groups = agrupar(entries)
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    anteriores = []
    for prev in index.get("editions", []):
        if prev == edition:
            continue
        arq = ROOT / "noticias" / (prev + ".json")
        if not arq.exists():
            continue
        try:
            doc = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for it in doc.get("items") or []:
            anteriores.append((prev[:10], notas.tokens(it.get("title") or ""), it.get("title") or ""))
    pesos = {c["key"]: c["weight"] for c in cfg["criteria"]}
    disponiveis = {p["nome"]: p.get("camp") for p in data.get("portais") or [] if p.get("manchetes")}
    byseg = defaultdict(list)
    for g in groups:
        por_nome = {}
        for e in g["items"]:
            por_nome.setdefault(e["name"], e)
        itens_unicos = list(por_nome.values())
        itens_unicos.sort(key=lambda e: (0 if e["name"] in notas.LINHA else 1, tiers.get(e["name"], 3), -len(notas.tokens(e["title"]))))
        lead = itens_unicos[0]
        lead_tok = notas.tokens(lead["title"])

        def acompanha(e):
            # Só entra no mesmo fato se o teste de títulos concordar. Dois nomes em comum
            # (injúria racial, amistoso, dívida) juntavam casos diferentes.
            return e is lead or notas.mesmo_fato(notas.tokens(e["title"]), lead_tok, e["title"], lead["title"])

        items = [e for e in itens_unicos if acompanha(e)]
        title = notas.limpa_manchete(lead["title"])
        seg = notas.ajustar_segmento(title, segmento_grupo(items), lead.get("hint") or "")
        if not seg:
            continue
        sconf = next((s for s in cfg["segments"] if s["key"] == seg), {})
        if len(items) < sconf.get("min_outlets", cfg["min_outlets"]):
            continue
        tk = notas.tokens(title)
        viu = any(notas.mesmo_fato(tk, ant, title, antigo) for _, ant, antigo in anteriores)
        atravessa = any(dia != edition[:10] and notas.mesmo_fato(tk, ant, title, antigo) for dia, ant, antigo in anteriores)
        if atravessa:
            continue
        outlets = [{"name": x["name"], "url": x["url"], "camp": x["camp"], "position": "editoria"} for x in items]
        if seg in ("politica", "economia", "seguranca", "internacional", "rio"):
            flag, note = notas.assimetria(outlets, disponiveis)
        else:
            flag, note = False, ""
        scores = {
            "cobertura": notas.cobertura(len(items)),
            "destaque": notas.destaque(outlets, tiers),
            "persistencia": notas.persistencia(False, viu and not atravessa, False),
            "peso_voce": 10 if seg in ("politica", "seguranca", "rio", "educacao", "economia") else 6,
        }
        ident = "-".join(sorted(tk)[:6])[:60] or "noticia"
        summary = resumo_de_itens(items)
        byseg[seg].append({
            "id": ident, "segment": seg, "title": title, "summary": summary,
            "scores": scores, "score": notas.nota_final(scores, pesos),
            "outlets": outlets, "asymmetry": {"flag": flag, "note": note if flag else ""},
        })
    selected = []
    usados = set()
    for s in cfg["segments"]:
        for it in sorted(byseg[s["key"]], key=lambda x: (-x["score"], -len(x["outlets"])))[:cfg["items_per_segment"]]:
            base, k = it["id"], 2
            while it["id"] in usados:
                it["id"] = f"{base}-{k}"
                k += 1
            usados.add(it["id"])
            selected.append(it)
    if len(selected) < 8:
        raise SystemExit("Poucos assuntos com cobertura suficiente: edição não publicada")
    fundidos = []
    for it in selected:
        alvo = None
        for s in fundidos:
            if notas.mesmo_fato(notas.tokens(it["title"]), notas.tokens(s["title"]), it["title"], s["title"]):
                alvo = s
                break
        if alvo is None:
            for s in fundidos:
                ea = {w for w in notas.tokens(it["title"]) if len(w) >= 5 and w not in notas.UBIQUOS}
                eb = {w for w in notas.tokens(s["title"]) if len(w) >= 5 and w not in notas.UBIQUOS}
                if len(ea & eb) >= 2 and not notas.conflito_numeros(it["title"], s["title"]):
                    alvo = s
                    break
        if alvo is None:
            fundidos.append(it)
            continue
        nomes = {o["name"] for o in alvo["outlets"]}
        for o in it["outlets"]:
            if o["name"] not in nomes:
                alvo["outlets"].append(o)
                nomes.add(o["name"])
        alvo["scores"]["cobertura"] = notas.cobertura(len(alvo["outlets"]))
        alvo["scores"]["destaque"] = notas.destaque(alvo["outlets"], tiers)
        alvo["score"] = notas.nota_final(alvo["scores"], pesos)
    selected = fundidos
    ordem = {s["key"]: i for i, s in enumerate(cfg["segments"])}
    selected.sort(key=lambda it: (-it["score"], ordem.get(it["segment"], 99)))
    top = selected[:cfg["top_overall"]]
    result = {
        "generated": agora.replace(microsecond=0).isoformat(),
        "gerador": "regras",
        "modelo": None,
        "headline": "; ".join(i["title"] for i in top[:3]),
        "top": [x["id"] for x in top],
        "items": selected,
        "x_trends": montar_trends(selected),
    }
    dest = ROOT / "noticias" / (edition + ".json")
    anterior = dest.read_text(encoding="utf-8") if dest.exists() else None
    idx_anterior = INDEX.read_text(encoding="utf-8")
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        rep = subprocess.run([sys.executable, str(ROOT / "tools/noticias_repeticao.py"), str(dest), "--aplicar"], cwd=ROOT, capture_output=True, text=True)
        print(rep.stdout.strip())
        final = json.loads(dest.read_text(encoding="utf-8"))
        if len(final.get("items") or []) < 8:
            raise SystemExit("edição por regras ficou com menos de 8 notícias depois da regra de não repetir")
        final["gerador"] = "regras"
        final["modelo"] = None
        dest.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        editions = sorted(set(json.loads(idx_anterior).get("editions", []) + [edition]))[-90:]
        INDEX.write_text(json.dumps({"editions": editions}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        if anterior is None:
            dest.unlink(missing_ok=True)
        else:
            dest.write_text(anterior, encoding="utf-8")
        INDEX.write_text(idx_anterior, encoding="utf-8")
        raise
    print(f"Gerada {dest.name}: {len(json.loads(dest.read_text(encoding='utf-8'))['items'])} assuntos; {len(entries)} manchetes analisadas")
    print(f"EDICAO={dest.name} GERADOR=regras")


if __name__ == "__main__":
    main()

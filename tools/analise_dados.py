"""Calcula os números de cada período (1, 7, 30, 60 e 90 dias) a partir de data.json.
A rotina de análise usa este resultado para escrever o texto; nenhum número é calculado de cabeça.
Uso: python3 tools/analise_dados.py > /tmp/dados_periodos.json"""
import json, os, statistics
from datetime import date, timedelta
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
D = json.load(open("data.json", encoding="utf-8"))
daily = [dict(zip(D["daily_columns"], r)) for r in D["daily"]]
posts = [dict(zip(D["columns"], r)) for r in D["posts"]]
reels = D.get("reels") or {}
styles = D.get("styles") or {}
end = date.fromisoformat(D["period"]["end"])
iso = lambda d: d.isoformat()
def med(v):
    v = [x for x in v if x is not None]; return statistics.median(v) if v else None
def soma(v):
    v = [x for x in v if x is not None]; return sum(v) if v else None
def pctd(a, b):
    return None if a is None or b in (None, 0) else round((a / b - 1) * 100, 1)
def janela(r, off=0):
    b = end - timedelta(days=r * off); a = b - timedelta(days=r - 1); return a, b
def conta(a, b):
    ds = [x for x in daily if iso(a) <= x["date"] <= iso(b)]
    out = {"dias_com_dado": len([x for x in ds if x.get("reach") is not None])}
    for k in ("views", "shares", "comments", "saves", "likes", "new_followers", "lost"):
        out[k] = soma(x.get(k) for x in ds); out[k + "_dias"] = len([x for x in ds if x.get(k) is not None])
    v = [x["reach"] for x in ds if x.get("reach") is not None]; out["alcance_medio_dia"] = round(sum(v) / len(v)) if v else None
    v = [x["accounts_engaged"] for x in ds if x.get("accounts_engaged") is not None]; out["contas_interacao_media_dia"] = round(sum(v) / len(v)) if v else None
    return out
def pubs(a, b):
    P = [p for p in posts if iso(a) <= p["ts"][:10] <= iso(b)]
    res = {"total": len(P), "por_formato": {}}
    for t, n in (("I", "imagem"), ("R", "reel"), ("C", "carrossel")):
        Q = [p for p in P if p["type"] == t]
        res["por_formato"][n] = {"posts": len(Q), "views_mediana": med([p["views"] for p in Q]), "views_soma": soma([p["views"] for p in Q]),
                                 "compart_mediana": med([p["shares"] for p in Q]), "seguidores_soma": soma([p["follows"] for p in Q])}
    def resumo(p):
        r = reels.get(p["code"]) or []
        return {"data": p["ts"][:16], "formato": p["type"], "titulo": p["title"], "tema": p["theme"], "estilo": styles.get(p["code"]),
                "views": p["views"], "alcance": p["reach"], "compart": p["shares"], "coment": p["comments"], "salvos": p["saves"], "seguiram": p["follows"],
                "reel_seg_assistidos": r[0] if len(r) > 0 else None, "reel_pulo_pct": r[1] if len(r) > 1 else None, "reel_duracao_s": r[3] if len(r) > 3 else None, "codigo": p["code"],
                "idade_h": p.get("age_h")}
    S = sorted(P, key=lambda p: p["views"] or 0, reverse=True)
    res["melhores"] = [resumo(p) for p in S[:3]]; res["piores"] = [resumo(p) for p in S[-3:][::-1]] if len(S) > 3 else []
    res["mais_seguidores"] = [resumo(p) for p in sorted([p for p in P if p["follows"]], key=lambda p: p["follows"], reverse=True)[:3]]
    res["mais_comentarios"] = [resumo(p) for p in sorted(P, key=lambda p: p["comments"] or 0, reverse=True)[:3]]
    horas = {}
    for p in P: horas.setdefault(p["ts"][11:13] + "h", []).append(p["views"] or 0)
    res["views_mediana_por_hora"] = {h: med(v) for h, v in sorted(horas.items())}
    temas = {}
    for p in P: temas.setdefault(p["theme"] or "outros", []).append(p["views"] or 0)
    res["views_mediana_por_tema"] = {k: {"posts": len(v), "mediana": med(v)} for k, v in temas.items()}
    return res
saida = {"fim": iso(end), "periodos": {}}
for r in (1, 7, 30, 60, 90):
    a, b = janela(r); pa, pb = janela(r, 1)
    c, cp = conta(a, b), conta(pa, pb)
    comp = {k: pctd(c.get(k), cp.get(k)) for k in ("views", "shares", "comments", "saves", "new_followers", "lost", "alcance_medio_dia", "contas_interacao_media_dia")}
    if r == 1:  # o dia contra a média dos 7 anteriores
        m7 = conta(a - timedelta(days=7), a - timedelta(days=1))
        comp = {k: pctd(c.get(k), (m7.get(k) / max(1, m7.get(k + "_dias") or 7)) if m7.get(k) is not None else None) for k in ("views", "shares", "comments", "saves", "new_followers", "lost")}
        comp["alcance_medio_dia"] = pctd(c["alcance_medio_dia"], m7["alcance_medio_dia"]); comp["contas_interacao_media_dia"] = pctd(c["contas_interacao_media_dia"], m7["contas_interacao_media_dia"])
    saida["periodos"][str(r)] = {"inicio": iso(a), "fim": iso(b), "comparado_com": ("média dos 7 dias anteriores" if r == 1 else f"{iso(pa)} a {iso(pb)}"),
                                 "conta": c, "conta_anterior": cp, "variacao_pct": comp, "publicacoes": pubs(a, b), "publicacoes_anterior": {"total": len([p for p in posts if iso(pa) <= p["ts"][:10] <= iso(pb)])}}
print(json.dumps(saida, ensure_ascii=False, indent=1))

"""Escreve analises/p{1,7,30,60,90}.json e atualiza analysis.json (semana + week_plan)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ia_comum as C
import modelos_ia

ROOT = C.ROOT
PERIODOS = (1, 7, 30, 60, 90)
KPI_FIXOS = [
    ("views", "Views recebidas pela conta", False),
    ("new_followers", "Novos seguidores", False),
    ("lost", "Deixaram de seguir", True),  # cair é bom
    ("shares", "Compartilhamentos recebidos", False),
    ("comments", "Comentários recebidos", False),
]
KPI_LIVRES = [
    ("saves", "Salvamentos recebidos", False),
    ("alcance_medio_dia", "Alcance médio por dia", False),
    ("contas_interacao_media_dia", "Contas com interação por dia", False),
]


def coletar_dados():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "analise_dados.py")], cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"analise_dados falhou: {(r.stderr or r.stdout)[:400]}")
    return json.loads(r.stdout)


def _kpi(label, valor, pct, inverter=False):
    delta, dire = C.fmt_delta(pct)
    if inverter and dire in {"up", "down"}:
        dire = "up" if dire == "down" else "down"
    return {"label": label, "value": C.fmt_br(valor) if valor is not None else "sem dado", "delta": delta if valor is not None else "sem comparação", "dir": dire if valor is not None else "na"}


def kpis_do_periodo(per):
    conta = per.get("conta") or {}
    var = per.get("variacao_pct") or {}
    kpis = []
    for chave, label, inv in KPI_FIXOS:
        val = conta.get(chave)
        kpis.append(_kpi(label, val, var.get(chave) if val is not None else None, inverter=inv))
    # sexto: o que mais mudou entre os livres (ou alcance)
    melhor = None
    for chave, label, inv in KPI_LIVRES:
        pct = var.get(chave)
        if pct is None or conta.get(chave) is None:
            continue
        if melhor is None or abs(pct) > abs(melhor[0]):
            melhor = (pct, chave, label, inv)
    if melhor:
        pct, chave, label, inv = melhor
        kpis.append(_kpi(label, conta.get(chave), pct, inverter=inv))
    else:
        kpis.append(_kpi("Alcance médio por dia", conta.get("alcance_medio_dia"), var.get("alcance_medio_dia")))
    return kpis[:6]


def _codigos(per):
    pubs = (per.get("publicacoes") or {})
    out = set()
    for lista in (pubs.get("melhores"), pubs.get("piores"), pubs.get("mais_seguidores"), pubs.get("mais_comentarios")):
        for p in lista or []:
            if p.get("codigo"):
                out.add(p["codigo"])
    return out


def _prompt_sistema():
    return """Você escreve a análise de desempenho do Instagram @rogerrio (Rogerio Pires, jornalista e comentarista político no Rio). Responda SOMENTE com um objeto JSON, sem markdown, neste formato:
{"periodos": {"1": {...}, "7": {...}, "30": {...}, "60": {...}, "90": {...}}, "week_plan": [...]}

Para cada período: headline (uma frase com números do insumo), cards (exatamente 4: os dois primeiros com title "O que funcionou" e "O que não funcionou"; os outros dois com título do que mais mudou). Cada card: title, status (good|warn|bad), status_label (Repetir|Ajustar|Atenção), diagnosis (2 a 3 frases citando posts e números do insumo), actions (2 a 4 ações concretas), posts (códigos citados, só os fornecidos).

Regras: português do Brasil, direto, trate o leitor como "você". Nunca travessão (— ou –). Todo número precisa estar no insumo. Não atribua causalidade ("esteve associado", "coincidiu"). Posts recentes ainda acumulam views; avise se comparar idades muito diferentes. Período de 1 dia: foque no dia e compare com a média da semana anterior. 60 e 90 dias: padrões e tendências, não posts isolados.

week_plan: lista de 7 dias, cada item ["Seg", [["r","8h","tema"], ...]], dias Seg a Dom. Tipos i, r, c, s. Horário de relógio (8h, 7h30) ou janela (Noite, Dia todo). Temas alinhados ao que funcionou no período de 7 dias, sem datas no lugar da hora. Sem travessão.
"""


def _normalizar_cards(cards, codigos):
    if not isinstance(cards, list) or len(cards) != 4:
        raise ValueError(f"precisa de 4 cards, veio {0 if not isinstance(cards, list) else len(cards)}")
    out = []
    for i, c in enumerate(cards):
        if not isinstance(c, dict):
            raise ValueError("card inválido")
        title = C.sem_travessao((c.get("title") or "").strip())
        if i == 0 and title != "O que funcionou":
            title = "O que funcionou"
        if i == 1 and title != "O que não funcionou":
            title = "O que não funcionou"
        status = (c.get("status") or "").strip()
        if status not in {"good", "warn", "bad"}:
            raise ValueError(f"status inválido em '{title}'")
        label = C.sem_travessao((c.get("status_label") or "").strip())
        if not label:
            label = {"good": "Repetir", "warn": "Ajustar", "bad": "Atenção"}[status]
        diagnosis = C.sem_travessao((c.get("diagnosis") or "").strip())
        actions = [C.sem_travessao(str(x).strip()) for x in (c.get("actions") or []) if str(x).strip()]
        if not diagnosis or len(actions) < 2:
            raise ValueError(f"card '{title}' incompleto")
        posts = [str(x).strip() for x in (c.get("posts") or []) if str(x).strip() and str(x).strip() in codigos]
        out.append({
            "title": title,
            "status": status,
            "status_label": label,
            "diagnosis": diagnosis,
            "actions": actions[:4],
            "posts": posts,
        })
    return out


def _normalizar_plan(plan):
    if not isinstance(plan, list) or len(plan) != 7:
        raise ValueError("week_plan precisa de 7 dias")
    dias = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    out = []
    for i, item in enumerate(plan):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("dia do week_plan inválido")
        nome, slots = item
        nome = "Sáb" if str(nome).startswith("Sab") else (dias[i] if str(nome) != dias[i] and str(nome)[:3] != dias[i][:3] else str(nome)[:3] if str(nome) != "Sáb" else "Sáb")
        # aceita "Sab" / "Sábado"
        br = {"Sab": "Sáb", "Sábado": "Sáb", "Sabado": "Sáb", "Sáb": "Sáb"}
        nome = br.get(str(item[0]), str(item[0]))
        if nome not in dias:
            nome = dias[i]
        slots_out = []
        for sl in slots or []:
            if not isinstance(sl, (list, tuple)) or len(sl) < 3:
                continue
            tipo = str(sl[0]).strip().lower()
            if tipo not in {"i", "r", "c", "s"}:
                continue
            hora = str(sl[1]).strip()
            # rejeita data no lugar da hora
            if C.DATA_RE.match(hora.replace(" ", "")):
                continue
            tema = C.sem_travessao(str(sl[2]).strip())
            if not hora or not tema:
                continue
            slots_out.append([tipo, hora, tema])
        if not slots_out:
            raise ValueError(f"week_plan sem slots em {nome}")
        out.append([nome, slots_out])
    if [d[0] for d in out] != dias:
        # força a ordem certa se os nomes vieram trocados mas há 7
        out = [[dias[i], out[i][1]] for i in range(7)]
    return out


def _compare_dict(per):
    texto = per.get("comparado_com") or ""
    # "2026-10-26 a 2026-11-01"
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*a\s*(\d{4}-\d{2}-\d{2})", texto)
    if m:
        return {"start": m.group(1), "end": m.group(2)}
    return {"start": per.get("inicio"), "end": per.get("fim")}


def montar_periodo(n, per, bruto, gerado):
    cards = _normalizar_cards((bruto or {}).get("cards") or [], _codigos(per))
    headline = C.sem_travessao((bruto or {}).get("headline") or "").strip()
    if not headline:
        raise ValueError(f"p{n} sem headline")
    doc = {
        "generated": gerado,
        "dias": n,
        "period": {"start": per.get("inicio"), "end": per.get("fim")},
        "compare": per.get("comparado_com") or "",
        "headline": headline,
        "kpis": kpis_do_periodo(per),
        "cards": cards,
    }
    if C.tem_travessao(doc):
        raise ValueError(f"travessão em p{n}")
    return doc


def gerar():
    dados = coletar_dados()
    agora = C.agora_sp()
    gerado = agora.replace(microsecond=0).isoformat()
    periodos = dados.get("periodos") or {}
    payload = {"generated": gerado, "periodos": {}}
    for n in PERIODOS:
        per = periodos.get(str(n)) or periodos.get(n) or {}
        payload["periodos"][str(n)] = {
            "inicio": per.get("inicio"),
            "fim": per.get("fim"),
            "comparado_com": per.get("comparado_com"),
            "conta": per.get("conta"),
            "variacao_pct": per.get("variacao_pct"),
            "publicacoes": per.get("publicacoes"),
            "publicacoes_anterior": per.get("publicacoes_anterior"),
        }
    aviso = None
    ultimo = None
    for tentativa in range(1, 4):
        user = "Escreva a análise de todos os períodos e o week_plan da semana seguinte. Use só os números do insumo.\n\n" + json.dumps(payload, ensure_ascii=False)
        if aviso:
            user += "\n\nCORREÇÃO:\n" + aviso
        try:
            bruto = modelos_ia.completar(
                [{"role": "system", "content": _prompt_sistema()}, {"role": "user", "content": user}],
                schema_nome="analises",
                max_tokens=7000,
                temperature=0.3,
                timeout=180,
            )
            blocos = bruto.get("periodos") or bruto
            docs = {}
            for n in PERIODOS:
                per = periodos.get(str(n)) or periodos.get(n) or {}
                b = blocos.get(str(n)) or blocos.get(n) or {}
                docs[n] = montar_periodo(n, per, b, gerado)
            plan = _normalizar_plan(bruto.get("week_plan") or [])
        except (modelos_ia.ModelError, ValueError, json.JSONDecodeError) as e:
            ultimo = e
            aviso = str(e)
            print(f"tentativa {tentativa} falhou: {e}", file=sys.stderr)
            continue
        (ROOT / "analises").mkdir(exist_ok=True)
        for n, doc in docs.items():
            C.gravar_json(ROOT / "analises" / f"p{n}.json", doc)
        # analysis.json: conteúdo de p7 + compare como {start,end} + week_plan
        p7 = dict(docs[7])
        semanal = {
            "generated": gerado,
            "period": p7["period"],
            "compare": _compare_dict(periodos.get("7") or periodos.get(7) or {}),
            "headline": p7["headline"],
            "kpis": p7["kpis"],
            "cards": p7["cards"],
            "week_plan": plan,
        }
        if C.tem_travessao(semanal):
            ultimo = ValueError("travessão no analysis.json")
            aviso = str(ultimo)
            continue
        C.gravar_json(ROOT / "analysis.json", semanal)
        print("análises gravadas em analises/p*.json e analysis.json")
        C.escrever_output(changed="1")
        return True
    raise SystemExit(f"não consegui gerar as análises: {ultimo}")


def _self_test():
    per = {
        "inicio": "2026-11-11", "fim": "2026-11-17",
        "comparado_com": "2026-11-04 a 2026-11-10",
        "conta": {"views": 1_800_000, "new_followers": 3056, "lost": 317, "shares": 95131, "comments": 4335, "alcance_medio_dia": 73000},
        "variacao_pct": {"views": -67, "new_followers": -83, "lost": -59, "shares": -5, "comments": -0.1, "alcance_medio_dia": -79},
        "publicacoes": {"melhores": [{"codigo": "abc123", "titulo": "Post"}]},
    }
    ks = kpis_do_periodo(per)
    assert len(ks) == 6
    assert ks[0]["value"] == "1,8 mi"
    assert ks[2]["dir"] == "up"  # menos gente deixou de seguir
    assert ks[2]["delta"].startswith(C.MENOS)
    cards = _normalizar_cards([
        {"title": "O que funcionou", "status": "good", "status_label": "Repetir", "diagnosis": "O Reel X trouxe 76 mil views.", "actions": ["Repetir o horário de 16h.", "Manter Reels longos de manhã."], "posts": ["abc123", "zzz"]},
        {"title": "O que não funcionou", "status": "bad", "status_label": "Atenção", "diagnosis": "Houve poucos posts.", "actions": ["Publicar mais cedo.", "Evitar buracos de 4 dias."], "posts": []},
        {"title": "Horários", "status": "warn", "status_label": "Ajustar", "diagnosis": "A mediana de 16h foi a mais alta.", "actions": ["Testar 16h de novo.", "Medir 19h também."], "posts": ["abc123"]},
        {"title": "Seguidores", "status": "warn", "status_label": "Ajustar", "diagnosis": "Entrada caiu com o volume.", "actions": ["Retomar o ritmo.", "Fechar com chamada para seguir."], "posts": []},
    ], {"abc123"})
    assert cards[0]["posts"] == ["abc123"]
    plan = _normalizar_plan([[d, [["r", "8h", "Reel de análise"], ["s", "Noite", "Enquete"]]] for d in ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")])
    assert plan[0][0] == "Seg" and plan[5][0] == "Sáb"
    try:
        _normalizar_plan([["Seg", [["r", "11/11", "tema"]]] ] + [[d, [["r", "8h", "t"]]] for d in ("Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")])
        # first day only has a date-like hour, should fail empty slots
        raise AssertionError("deveria recusar hora=11/11")
    except ValueError:
        pass
    print("gerar_analises: ok")


def main():
    if "--self-test" in sys.argv:
        _self_test()
        return
    gerar()


if __name__ == "__main__":
    main()

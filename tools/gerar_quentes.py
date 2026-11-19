"""Alerta de pauta quente e rede de segurança dos pedidos 'Virar pauta'."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ia_comum as C
import modelos_ia

ROOT = C.ROOT
MAX_DIA = 3
MAX_ALERTAS = 30
PROTAG = (
    "flávio", "flavio", "bolsonaro", "lula", "moraes", "stf", "tse", "pf ",
    "pl ", "pt ", "michelle", "eduardo bolsonaro", "rio",
)


def _edicoes():
    atual_n, atual = C.edicao_recente(0)
    ant_n, ant = C.edicao_recente(1)
    return atual_n, atual, ant_n, ant


def _ids(edicao):
    return {it.get("id") for it in (edicao or {}).get("items") or [] if it.get("id")}


def _candidatos(atual, anterior, agora):
    """Candidatos só com timestamp confirmável nas últimas 3 horas."""
    ids_ant = _ids(anterior)
    itens, _ = C.resumir_noticias(atual, limite=40)
    out = []
    gerado = C.parse_iso((atual or {}).get("generated") or "")
    for it in itens:
        novo = it.get("id") not in ids_ant or it.get("desdobramento")
        # tenta data nas páginas (não no Google News, que não traz horário)
        published = None
        fontes_ok = []
        for o in it.get("outlets") or []:
            url = (o.get("url") or "").strip()
            if not url or "news.google.com" in url:
                continue
            fontes_ok.append(o)
            if not published:
                pag = C.ler_pagina(url)
                if pag and pag.get("published") and C.fresco(pag["published"], agora):
                    published = pag["published"]
                    if pag.get("title"):
                        o.setdefault("page_title", pag["title"])
        if not published:
            # só aceita o horário da edição se ela mesma nasceu nas últimas 3h
            # (ainda assim é teto, não prova da 1ª publicação; na dúvida, não alerte)
            continue
        if not C.fresco(published, agora):
            continue
        out.append({**it, "published": published, "novo_na_edicao": bool(novo), "fontes_lidas": fontes_ok[:4]})
    return out


def _prompt_sistema():
    voz = C.texto(ROOT / "prompts" / "voz-editorial.md")
    estilo = C.texto(ROOT / "prompts" / "estilo-imagem.md")
    return f"""Você é o monitor de pauta quente de Rogerio Pires (jornalista e comentarista político no Rio; linha de direita conservadora). Responda SOMENTE com um objeto JSON {{"alerts":[...]}} sem markdown.

VOZ EDITORIAL (como escrever, nunca para decidir SE alerta):
{voz}

ESTILO DE IMAGEM (se houver image_prompt):
{estilo}

Só alerte fatos que já vieram nos insumos com "published" dentro das últimas 3 horas. Se a lista de candidatos estiver vazia, devolva {{"alerts":[]}}. Não invente fato, horário ou url.

Critérios (todos): frescor já conferido; fato em pelo menos 2 veículos ou fonte identificada (deixe claro quem afirma); grande repercussão nacional ou no Rio; o público vai cobrar reação (também fato ruim para Flávio, Bolsonaro, PL ou a direita); ainda não alertado, salvo desdobramento que muda a história.

Cada alerta: title (manchete direta, sujeito e verbo), fact (2 a 3 frases), sources ({{name,url}} só com urls fornecidas, 2 se possível), suggestion (formato e horário), hook, script (teleprompter até 30s, fecha com pergunta ou pedido), image_prompt (opcional, sem travessão), timeline {{rende (alto/médio/baixo), angle}}, published (copie o ISO informado, não invente). O campo time é preenchido pelo script.

Português do Brasil. Nunca travessão (— ou –). Nunca afirme crime sem condenação.
"""


def _ja_alertados(doc, dia):
    titulos = []
    n_hoje = 0
    for a in doc.get("alerts") or []:
        titulos.append((a.get("title") or "").strip().lower())
        t = C.parse_iso(a.get("time") or "")
        if t and t.date() == dia:
            n_hoje += 1
    return titulos, n_hoje


def _normalizar_alerta(bruto, permitidas, agora, publicados_ok):
    if not isinstance(bruto, dict):
        raise ValueError("alerta inválido")
    title = C.sem_travessao((bruto.get("title") or "").strip())
    fact = C.sem_travessao((bruto.get("fact") or "").strip())
    if not title or not fact:
        raise ValueError("alerta sem title ou fact")
    fontes = C.filtrar_fontes(bruto.get("sources") or [], permitidas, minimo=1, maximo=4)
    if not fontes:
        raise ValueError(f"alerta '{title}' sem fonte válida")
    published = bruto.get("published") or ""
    if published not in publicados_ok and not C.fresco(published, agora):
        raise ValueError(f"alerta '{title}' sem published fresco confirmado")
    # aceita o ISO canônico equivalente
    if not C.fresco(published, agora):
        raise ValueError(f"alerta '{title}' fora da janela de 3 horas")
    script = C.sem_travessao((bruto.get("script") or "").strip())
    hook = C.sem_travessao((bruto.get("hook") or "").strip())
    if not script or not hook:
        raise ValueError(f"alerta '{title}' sem hook ou script")
    tl = bruto.get("timeline") or {}
    if not isinstance(tl, dict):
        tl = {}
    rende = tl.get("rende") or "médio"
    if isinstance(rende, bool):
        rende = "alto" if rende else "baixo"
    angle = C.sem_travessao((tl.get("angle") or title).strip())
    out = {
        "time": C.agora_sp(agora).replace(microsecond=0).isoformat(),
        "published": C.parse_iso(published).isoformat(),
        "title": title,
        "fact": fact,
        "sources": fontes,
        "suggestion": C.sem_travessao((bruto.get("suggestion") or "").strip()),
        "hook": hook,
        "script": script,
        "timeline": {"rende": str(rende), "angle": angle},
    }
    if bruto.get("image_prompt"):
        out["image_prompt"] = C.sem_travessao(str(bruto.get("image_prompt")).strip())
        if bruto.get("style"):
            out["style"] = str(bruto.get("style")).strip()
    if C.tem_travessao(out):
        raise ValueError("travessão no alerta")
    return out


def gerar_alertas(agora=None):
    agora = C.agora_sp(agora)
    path = ROOT / "pautas" / "quentes.json"
    doc = C.ler_json(path) or {"alerts": []}
    atuais = list(doc.get("alerts") or [])
    titulos, n_hoje = _ja_alertados(doc, agora.date())
    if n_hoje >= MAX_DIA:
        print("já há 3 alertas hoje; nada a acrescentar")
        return False
    atual_n, atual, ant_n, ant = _edicoes()
    cands = _candidatos(atual, ant, agora)
    if not cands:
        print("nenhum candidato com horário de publicação confirmado nas últimas 3 horas")
        return False
    permitidas = C.urls_de(cands, C.resumir_noticias(atual)[0], C.trends_arquivo())
    publicados_ok = {c["published"] for c in cands if c.get("published")}
    pautas = []
    for nome in sorted((ROOT / "pautas").glob("20*.json"))[-3:]:
        d = C.ler_json(nome) or {}
        pautas.append({"date": d.get("date"), "summary": (d.get("summary") or "")[:400]})
    sob = C.ler_json(ROOT / "pautas" / "sob-demanda.json") or {}
    sob_titulos = [((it.get("source") or {}).get("title") or "") for it in (sob.get("items") or [])[:10]]
    payload = {
        "agora": agora.replace(microsecond=0).isoformat(),
        "edicao": atual_n,
        "edicao_anterior": ant_n,
        "candidatos_frescos": cands,
        "trends_x": C.trends_arquivo(),
        "alertas_hoje": n_hoje,
        "alertas_recentes": [{"time": a.get("time"), "title": a.get("title")} for a in atuais[:15]],
        "pautas_recentes": pautas,
        "virar_pauta_recentes": sob_titulos,
        "max_nesta_execucao": MAX_DIA - n_hoje,
    }
    aviso = None
    ultimo = None
    for tentativa in range(1, 4):
        user = "Decida os alertas. Só use candidatos com published fresco. Se nenhum merecer, alerts vazio.\n\n" + json.dumps(payload, ensure_ascii=False)
        if aviso:
            user += "\n\nCORREÇÃO:\n" + aviso
        try:
            bruto = modelos_ia.completar(
                [{"role": "system", "content": _prompt_sistema()}, {"role": "user", "content": user}],
                schema_nome="quentes",
                max_tokens=4000,
                temperature=0.2,
                timeout=150,
            )
            novos = []
            for a in (bruto.get("alerts") or []):
                n = _normalizar_alerta(a, permitidas, agora, publicados_ok)
                if n["title"].strip().lower() in titulos:
                    continue
                novos.append(n)
                if len(novos) + n_hoje >= MAX_DIA:
                    break
        except (modelos_ia.ModelError, ValueError, json.JSONDecodeError) as e:
            ultimo = e
            aviso = str(e)
            print(f"tentativa {tentativa} falhou: {e}", file=sys.stderr)
            continue
        if not novos:
            print("modelo não propôs alerta válido (ou lista vazia)")
            return False
        doc["alerts"] = (novos + atuais)[:MAX_ALERTAS]
        C.gravar_json(path, doc)
        print(f"acrescentei {len(novos)} alerta(s) em {path}")
        return True
    raise SystemExit(f"não consegui gerar alerta quente: {ultimo}")


def gerar(agora=None, com_sob=True):
    mudou_q = gerar_alertas(agora=agora)
    mudou_s = False
    if com_sob:
        try:
            import gerar_sob_demanda
            mudou_s = bool(gerar_sob_demanda.gerar(obrigatorio=False))
        except Exception as e:
            # rede de segurança não pode derrubar o alerta já gravado
            print(f"aviso: sob demanda falhou: {e}", file=sys.stderr)
    mudou = bool(mudou_q or mudou_s)
    C.escrever_output(changed="1" if mudou else "0", quentes="1" if mudou_q else "0", sob="1" if mudou_s else "0")
    return mudou


def _self_test():
    agora = datetime(2026, 11, 19, 11, 52, tzinfo=C.BRT)
    ok = _normalizar_alerta({
        "title": "STF suspende julgamento nesta manhã",
        "fact": "O Supremo interrompeu o julgamento nesta manhã. A decisão sai só na próxima sessão.",
        "published": "2026-11-19T10:00:00-03:00",
        "sources": [{"name": "Poder360", "url": "https://www.poder360.com.br/x"}],
        "suggestion": "Story agora e reel às 14h.",
        "hook": "O STF parou o julgamento no meio da manhã.",
        "script": "O Supremo suspendeu o julgamento nesta manhã. A decisão ficou para a próxima sessão. Você acha que isso muda o quadro?",
        "timeline": {"rende": "médio", "angle": "Suspensão no STF."},
    }, {"https://www.poder360.com.br/x"}, agora, {"2026-11-19T10:00:00-03:00"})
    assert ok["time"].endswith("-03:00")
    try:
        _normalizar_alerta({**{"title": "Velho", "fact": "Fato velho.", "published": "2026-11-18T10:00:00-03:00",
            "sources": [{"name": "A", "url": "https://www.poder360.com.br/x"}],
            "hook": "h", "script": "s", "timeline": {"rende": "baixo", "angle": "a"}}},
            {"https://www.poder360.com.br/x"}, agora, set())
        raise AssertionError("deveria recusar published velho")
    except ValueError:
        pass
    print("gerar_quentes: ok")


def main():
    if "--self-test" in sys.argv:
        _self_test()
        return
    gerar(com_sob="--sem-sob" not in sys.argv)


if __name__ == "__main__":
    main()

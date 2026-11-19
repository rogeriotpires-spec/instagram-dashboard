"""Gera a pauta diária de conteúdo do Instagram com o cliente de IA das notícias.

Substitui o Claude Code Action do workflow pautas.yml. Lê os mesmos insumos
de prompts/pautas.md, chama modelos_ia.completar (OpenAI ou GitHub Models) e
grava pautas/AAAA-MM-DD.json e pautas/index.json. Não faz commit nem push:
isso fica a cargo do workflow.

Uso:
  python3 tools/gerar_pauta.py
  python3 tools/gerar_pauta.py --data 2026-11-20
  python3 tools/gerar_pauta.py --self-test
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import modelos_ia

ROOT = Path(__file__).resolve().parent.parent
BRT = timezone(timedelta(hours=-3))
WD = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
TIPOS = {"i": "imagem", "r": "reel", "c": "carrossel", "s": "stories"}
RISCOS = {"baixo", "médio", "medio", "alto"}
# Horário de relógio (hora marcada): 7h, 08h, 14h30. Não aceita data (11/11).
HORA_RE = re.compile(r"^([01]?\d|2[0-3])h([0-5]\d)?$")
HORA_COLON_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DATA_RE = re.compile(r"^\d{1,2}/\d{1,2}(/\d{2,4})?$")
HORA_NO_TEXTO_RE = re.compile(
    r"(?:às|as|as)\s+(\d{1,2})h(?:([0-5]\d))?"
    r"|(\d{1,2})h([0-5]\d)?"
    r"|(\d{1,2}):([0-5]\d)",
    re.I,
)
TRAVESSAO_RE = re.compile(r"[—–]")
# Sobras de Ela/novela/horóscopo que a classificação ainda pode mandar como política.
ENTRETENIMENTO_RE = re.compile(
    r"\b(unha|unhas|novela|hor[oó]scopo|moda|beleza|celebridade|"
    r"reality|bbb|a fazenda|anitta)\b",
    re.I,
)
SLOTS_PADRAO = [
    ("r", "8h", "Reel com o fato mais forte do dia"),
    ("s", "Noite", "Stories com enquete sobre o tema do Reel"),
]


def agora_sp(agora=None):
    if agora is None:
        agora = datetime.now(BRT)
    elif agora.tzinfo is None:
        agora = agora.replace(tzinfo=BRT)
    else:
        agora = agora.astimezone(BRT)
    return agora


def data_da_pauta(agora=None):
    """Amanhã, salvo se ainda for antes das 6h em Brasília: aí é hoje."""
    agora = agora_sp(agora)
    dia = agora.date() if agora.hour < 6 else (agora + timedelta(days=1)).date()
    return dia


def weekday_de(dia):
    return WD[dia.weekday()]


def _ler_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _texto(path, padrao=""):
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else padrao


def sem_travessao(valor):
    if isinstance(valor, str):
        return TRAVESSAO_RE.sub("-", valor)
    if isinstance(valor, list):
        return [sem_travessao(x) for x in valor]
    if isinstance(valor, dict):
        return {k: sem_travessao(v) for k, v in valor.items()}
    return valor


def normalizar_hora(valor, texto=""):
    """Devolve hora no formato 7h / 14h30, ou None se não for horário de relógio."""
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
    # Data no campo de hora (ex.: "11/11") não serve; tenta achar hora no texto.
    if DATA_RE.match((valor or "").strip()) or not HORA_RE.match(v or "x"):
        for m in HORA_NO_TEXTO_RE.finditer(texto or ""):
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
    """Garante hora marcada em cada item; descarta data no lugar da hora."""
    out = []
    for item in agenda or []:
        if not isinstance(item, dict):
            continue
        texto = item.get("text") or item.get("texto") or ""
        hora = normalizar_hora(item.get("time") or item.get("hora") or "", texto)
        if not hora:
            print(
                f"aviso: item de agenda sem hora marcada ignorado "
                f"(time={item.get('time')!r})",
                file=sys.stderr,
            )
            continue
        src = item.get("source") or item.get("fonte") or {}
        if not isinstance(src, dict):
            src = {}
        out.append({
            "time": hora,
            "text": sem_travessao(texto).strip(),
            "source": {
                "name": (src.get("name") or src.get("nome") or "").strip(),
                "url": (src.get("url") or "").strip(),
            },
        })
    return out


def slots_do_plano(analysis, dia):
    """Usa tipo e horário do week_plan; o texto do tema pode estar defasado."""
    wd = weekday_de(dia)
    plano = (analysis or {}).get("week_plan") or []
    for row in plano:
        if not row:
            continue
        if str(row[0]).startswith(wd):
            brutos = row[1] if len(row) > 1 else []
            out = []
            for item in brutos:
                if not item or len(item) < 2:
                    continue
                tipo, hora = item[0], item[1]
                if tipo not in TIPOS:
                    continue
                desc = item[2] if len(item) > 2 else TIPOS[tipo]
                out.append({"time": hora, "type": tipo, "kind": desc})
            if out:
                return out
    return [{"time": t, "type": tp, "kind": k} for tp, t, k in SLOTS_PADRAO]


def _seguidores():
    path = ROOT / "data.json"
    if not path.exists():
        return None
    try:
        acc = _ler_json(path).get("account") or {}
        n = acc.get("followers")
        return int(n) if n is not None else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _edicao_recente():
    ix = ROOT / "noticias" / "index.json"
    if not ix.exists():
        return None, None
    dias = _ler_json(ix).get("editions") or []
    if not dias:
        return None, None
    nome = dias[-1]
    path = ROOT / "noticias" / f"{nome}.json"
    if not path.exists():
        return nome, None
    return nome, _ler_json(path)


def _pautas_recentes(dia, n=5):
    pasta = ROOT / "pautas"
    arquivos = sorted(pasta.glob("20[0-9][0-9]-[01][0-9]-[0-3][0-9].json"))
    escolhidos = []
    for path in reversed(arquivos):
        if path.stem >= dia.isoformat():
            continue
        escolhidos.append(path)
        if len(escolhidos) >= n:
            break
    return list(reversed(escolhidos))


def _resumo_pauta(path):
    d = _ler_json(path)
    opcoes = []
    for s in d.get("slots") or []:
        for o in s.get("options") or []:
            opcoes.append({
                "time": s.get("time"),
                "type": s.get("type"),
                "title": o.get("title"),
                "fact": (o.get("fact") or "")[:280],
            })
    return {
        "date": d.get("date") or path.stem,
        "summary": (d.get("summary") or "")[:400],
        "excluidos": d.get("excluidos") or [],
        "opcoes": opcoes,
    }


def _excluidos_anteriores(dia, sob, quentes):
    titulos = []
    for k in range(0, 8):
        f = ROOT / "pautas" / f"{(dia - timedelta(days=k)).isoformat()}.json"
        if not f.exists():
            continue
        for x in _ler_json(f).get("excluidos") or []:
            t = x.get("titulo") or x.get("title") or ""
            if t:
                titulos.append(t)
    for x in (sob or {}).get("excluidos") or []:
        t = x.get("titulo") or x.get("title") or ""
        if t:
            titulos.append(t)
    for x in (quentes or {}).get("excluidos") or []:
        t = x.get("titulo") or x.get("title") or ""
        if t:
            titulos.append(t)
    # únicos, ordem preservada
    vistos, out = set(), []
    for t in titulos:
        k = t.strip().lower()
        if k and k not in vistos:
            vistos.add(k)
            out.append(t)
    return out


def _janela_dias(dia, quando):
    if not quando:
        return False
    q = str(quando)[:10]
    return (dia - timedelta(days=3)).isoformat() <= q < dia.isoformat()


def _resumo_sob(sob, dia):
    out = []
    for x in (sob or {}).get("items") or []:
        quando = (x.get("done") or x.get("requested") or "")[:10]
        if not _janela_dias(dia, quando):
            continue
        for o in x.get("options") or []:
            out.append({"quando": quando, "title": o.get("title"), "fact": (o.get("fact") or "")[:220]})
    return out[:20]


def _resumo_quentes(quentes, dia):
    out = []
    for x in (quentes or {}).get("alerts") or []:
        quando = (x.get("time") or x.get("published") or "")[:10]
        if not _janela_dias(dia, quando):
            continue
        out.append({"quando": quando, "title": x.get("title"), "fact": (x.get("fact") or "")[:220]})
    return out[:20]


def _entretenimento(titulo, resumo=""):
    return bool(ENTRETENIMENTO_RE.search((titulo or "") + " " + (resumo or "")))


def _resumo_noticias(edicao):
    if not edicao:
        return [], [], []
    itens = []
    pulados = []
    for it in edicao.get("items") or []:
        titulo = it.get("title") or ""
        resumo = it.get("summary") or ""
        if _entretenimento(titulo, resumo):
            pulados.append(titulo)
            continue
        outlets = []
        for o in (it.get("outlets") or it.get("sources") or [])[:3]:
            if isinstance(o, dict) and (o.get("url") or o.get("name")):
                outlets.append({"name": o.get("name") or o.get("nome") or "", "url": o.get("url") or ""})
        itens.append({
            "id": it.get("id"),
            "segment": it.get("segment"),
            "score": it.get("score") if it.get("score") is not None else it.get("nota"),
            "title": titulo,
            "summary": resumo[:500],
            "outlets": outlets,
            "desdobramento": it.get("desdobramento") or None,
        })
    itens.sort(key=lambda x: float(x["score"] or 0), reverse=True)
    trends = []
    for t in edicao.get("x_trends") or []:
        fonte = t.get("fonte") or t.get("source") or {}
        trends.append({
            "nome": t.get("nome") or t.get("name"),
            "sobre": t.get("sobre") or "",
            "leitura": t.get("leitura") or "",
            "uso": t.get("uso") or "",
            "fonte": {
                "name": (fonte.get("name") or fonte.get("nome") or "") if isinstance(fonte, dict) else "",
                "url": (fonte.get("url") or "") if isinstance(fonte, dict) else "",
            },
        })
    return itens[:24], trends, pulados


def _urls_permitidas(*blocos):
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


def _trends_arquivo():
    path = ROOT / "noticias" / "trends.json"
    if not path.exists():
        return None
    try:
        d = _ler_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return {
        "coletado": d.get("coletado"),
        "fonte": d.get("fonte"),
        "trends": [{"nome": t.get("nome"), "volume": t.get("volume")} for t in (d.get("trends") or [])[:20]],
    }


def _cards_analise(analysis):
    out = []
    for c in (analysis or {}).get("cards") or []:
        out.append({
            "title": c.get("title"),
            "status": c.get("status"),
            "diagnosis": (c.get("diagnosis") or "")[:400],
        })
    return out[:6]


def carregar_insumos(dia):
    analysis = _ler_json(ROOT / "analysis.json") if (ROOT / "analysis.json").exists() else {}
    sob = _ler_json(ROOT / "pautas" / "sob-demanda.json") if (ROOT / "pautas" / "sob-demanda.json").exists() else {}
    quentes = _ler_json(ROOT / "pautas" / "quentes.json") if (ROOT / "pautas" / "quentes.json").exists() else {}
    ed_nome, edicao = _edicao_recente()
    noticias, x_trends, pulados = _resumo_noticias(edicao)
    recentes = [_resumo_pauta(p) for p in _pautas_recentes(dia)]
    excluidos = _excluidos_anteriores(dia, sob, quentes)
    existente = ROOT / "pautas" / f"{dia.isoformat()}.json"
    excluidos_hoje = []
    if existente.exists():
        excluidos_hoje = _ler_json(existente).get("excluidos") or []
        for x in excluidos_hoje:
            t = x.get("titulo") or x.get("title") or ""
            if t and t not in excluidos:
                excluidos.append(t)
    return {
        "analysis": analysis,
        "slots": slots_do_plano(analysis, dia),
        "cards": _cards_analise(analysis),
        "headline_analise": (analysis or {}).get("headline"),
        "pautas_recentes": recentes,
        "sob": _resumo_sob(sob, dia),
        "quentes": _resumo_quentes(quentes, dia),
        "excluidos": excluidos,
        "excluidos_hoje": excluidos_hoje,
        "edicao_nome": ed_nome,
        "edicao_quando": (edicao or {}).get("generated") if edicao else None,
        "headline_noticias": (edicao or {}).get("headline") if edicao else None,
        "noticias": noticias,
        "x_trends": x_trends,
        "entretenimento_ignorado": pulados,
        "trends_x": _trends_arquivo(),
        "seguidores": _seguidores(),
    }


def _prompt_sistema(seguidores):
    voz = _texto(ROOT / "prompts" / "voz-editorial.md")
    estilo = _texto(ROOT / "prompts" / "estilo-imagem.md")
    publico = f"cerca de {seguidores:,} seguidores".replace(",", ".") if seguidores else "cerca de 80 mil seguidores"
    return f"""Você é o pauteiro de Rogerio Pires (@rogerrio, Instagram verificado, {publico}). Ele é jornalista, professor, gestor público no Rio de Janeiro, comentarista da Jovem Pan News Rio e escreve na Revista Timeline. Linha editorial: direita conservadora, crítica ao governo Lula, ao PT, ao ativismo do STF e à grande mídia; apoio a Flávio Bolsonaro e ao PL. Tom: contundente na maior parte das pautas, variando conforme o tema (irônico, sóbrio, emocional). Público: maioria acima de 45 anos, Rio e São Paulo à frente, compartilha muito por mensagem.

Responda SOMENTE com um objeto JSON da pauta, sem markdown e sem texto fora do JSON.

VOZ EDITORIAL OBRIGATÓRIA:
{voz}

ESTILO DAS IMAGENS (vale para option de imagem e capa de carrossel, inclusive o campo "style"):
{estilo}

REGRAS DE SAÍDA:
- O objeto tem: date (AAAA-MM-DD), weekday (Seg/Ter/Qua/Qui/Sex/Sáb/Dom), generated (já informado; copie-o), summary (1 ou 2 frases), agenda (lista), slots (lista), excluidos (copie a lista recebida, sem alterar).
- agenda: só compromissos do DIA da pauta com hora marcada. Campo "time" é relógio (exemplos: "10h", "14h30", "7h"). NUNCA coloque data nesse campo (proibido: "11/11", "8/11", "8/10"). Se não houver horário conhecido, o item não entra na agenda; a data pode ir no summary.
- Cada item de agenda: {{"time": "14h", "text": "...", "source": {{"name": "...", "url": "..."}}}}. A url tem de ser uma das urls fornecidas.
- slots: use exatamente os slots informados (mesmo time e type). kind pode ser reescrito para o fato de hoje, sem repetir tema velho do plano (Enem, feriado já passado, etc.).
- Cada slot tem exatamente DUAS options (A e B) com temas diferentes.
- Todo fato tem 1 a 3 sources com name e url. Use SOMENTE urls fornecidas nos insumos. Nunca invente link, número, data ou citação.
- Imagem (type i): art_text (linhas exatas da arte, incluindo "Siga @rogerrio (Instagram) @rogerrioX (X)"), faces, prompt completo em português para o ChatGPT, style (ilustracao, realista, foto-oficial, cartaz ou print), caption curta terminando em pergunta fechada.
- Reel (type r): duration (notícia ou reação até 30s; análise até 60s; teste com 2 ganchos em hooks), script corrido para teleprompter (frase mais forte primeiro, fecha com pergunta fechada ou pedido de envio), screen_text com 2 a 4 linhas, hooks quando for teste.
- Carrossel (type c): slides com 6 a 10 strings (capa com gancho, um dado por slide, último pedindo salvar ou enviar); prompt e art_text para a capa; style.
- Stories (type s): poll com question e options (duas respostas curtas). slides opcionais de resumo.
- risk: OBRIGATÓRIO e só um destes valores exatos: "baixo", "médio" ou "alto" (nunca vazio). risk_note objetiva. Nunca afirme crime sem condenação.

FORMATO OBRIGATÓRIO DE CADA OPTION (use exatamente estes nomes de chave, em inglês; nunca deixe title, fact, sources, caption ou risk vazios):
{{"title": "manchete", "tone": "contundente", "fact": "o fato em 1 ou 2 frases", "sources": [{{"name": "Veículo", "url": "url dos insumos"}}], "caption": "legenda terminando em pergunta fechada", "risk": "baixo", "risk_note": "...", "timeline": {{"rende": false, "motivo": "..."}}, ...campos do tipo do slot}}
Cada slot: {{"time": "...", "type": "...", "kind": "...", "options": [option A, option B]}}. "options" é uma LISTA com dois objetos, nunca um objeto com chaves A e B.
- timeline: {{"rende": true, "angle": "..."}} quando der artigo de 600 a 1.200 palavras; {{"rende": false, "motivo": "..."}} caso contrário.
- title: manchete direta, sujeito e verbo, fiel ao que os jornais publicaram, sem metáfora nem enigma, até cerca de 90 caracteres. Provocação fica na arte, nos ganchos e na legenda. Português do Brasil. NUNCA use travessão (— ou –). Primeira pessoa quando for fala dele.
- Não repita tema ou fato já pautado nos 3 dias anteriores, nem itens excluídos, nem pedidos sob demanda ou pautas quentes da janela. Exceção: desdobramento novo de verdade (fato novo nas últimas 24 horas). Aí o title e o fact giram em torno do fato novo e a option leva "desdobramento": "o fato novo, em uma frase". Carrossel de resumo da semana leva "desdobramento": "resumo da semana" e traz pelo menos 2 fatos novos.
- Ignore notícias que sejam claramente moda, novela, horóscopo, celebridade ou lifestyle sem interesse público, mesmo que apareçam na lista.
- Priorize temas com histórico de bom desempenho: STF, corrupção, economia no bolso, imprensa, eleição, segurança pública, Rio de Janeiro. Um fato forte que também está em alta no X ganha prioridade; um ataque à direita em alta pode virar pauta de resposta com fato.
- Fontes só no campo sources. No máximo uma atribuição por texto.
"""


def _prompt_usuario(dia, gerado, insumos, aviso=None):
    payload = {
        "date": dia.isoformat(),
        "weekday": weekday_de(dia),
        "generated": gerado,
        "slots_obrigatorios": insumos["slots"],
        "headline_analise": insumos.get("headline_analise"),
        "cards_analise": insumos.get("cards"),
        "edicao_noticias": insumos.get("edicao_nome"),
        "edicao_quando": insumos.get("edicao_quando"),
        "headline_noticias": insumos.get("headline_noticias"),
        "noticias": insumos["noticias"],
        "x_trends_da_edicao": insumos["x_trends"],
        "trends_x_coletados_agora": insumos.get("trends_x"),
        "pautas_recentes": insumos["pautas_recentes"],
        "sob_demanda_3_dias": insumos["sob"],
        "pautas_quentes_3_dias": insumos["quentes"],
        "excluidos": insumos["excluidos"],
        "entretenimento_ignorado": insumos.get("entretenimento_ignorado") or [],
    }
    extra = ""
    if aviso:
        extra = (
            "\n\nCORREÇÃO OBRIGATÓRIA: a tentativa anterior falhou. "
            "Reescreva a pauta inteira, corrigindo isto:\n"
            + aviso
        )
    return (
        "Monte a pauta do dia indicado. Os fatos e as urls abaixo são o único "
        "material autorizado; não invente fonte nem busque na web. Se a agenda "
        "do dia não tiver horário conhecido nos insumos, deixe a agenda curta "
        "ou vazia, mas nunca invente um horário.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + extra
    )


def _filtrar_fontes(sources, permitidas):
    out = []
    vistos = set()
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        url = (s.get("url") or "").strip()
        name = (s.get("name") or s.get("nome") or "").strip()
        if url not in permitidas:
            continue
        if url in vistos:
            continue
        vistos.add(url)
        out.append({"name": name, "url": url})
        if len(out) >= 3:
            break
    return out


# Chaves alternativas que o modelo às vezes usa no lugar das oficiais.
CHAVES_TITLE = ("title", "titulo", "título", "manchete", "headline", "tema")
CHAVES_FACT = ("fact", "fato", "fatos", "facts", "resumo_fato", "descricao", "descrição", "contexto")
# Variações de risco aceitas; vazio ou desconhecido vira "médio" (cautela).
RISCO_MAP = {
    "baixo": "baixo", "low": "baixo", "baixa": "baixo", "pequeno": "baixo",
    "médio": "médio", "medio": "médio", "média": "médio", "media": "médio",
    "medium": "médio", "moderado": "médio", "moderate": "médio",
    "alto": "alto", "alta": "alto", "high": "alto", "elevado": "alto",
}
RISCO_PADRAO = "médio"


def _primeiro_texto(opt, chaves):
    for k in chaves:
        v = opt.get(k)
        if isinstance(v, list):
            v = " ".join(str(x) for x in v if str(x).strip())
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _titulo_do_fato(fact, limite=90):
    """Manchete de emergência: primeira frase do fato, cortada em ~90 caracteres."""
    frase = re.split(r"(?<=[.!?])\s+", fact.strip(), maxsplit=1)[0].rstrip(".")
    if len(frase) > limite:
        frase = frase[:limite].rsplit(" ", 1)[0]
    return frase


def _desembrulhar_option(opt):
    """Aceita {"A": {...}} ou {"option": {...}} quando o modelo embrulha a option."""
    if isinstance(opt, dict) and len(opt) == 1:
        (unico,) = opt.values()
        if isinstance(unico, dict) and any(k in unico for k in CHAVES_TITLE + CHAVES_FACT):
            return unico
    return opt


def _normalizar_risco(valor):
    v = str(valor or "").strip().lower()
    return RISCO_MAP.get(v), v


def _normalizar_option(opt, tipo, permitidas):
    opt = _desembrulhar_option(opt)
    if not isinstance(opt, dict):
        raise ValueError("option não é objeto")
    title = sem_travessao(_primeiro_texto(opt, CHAVES_TITLE))
    fact = sem_travessao(_primeiro_texto(opt, CHAVES_FACT))
    if not fact:
        chaves = ", ".join(sorted(opt.keys())) or "nenhuma"
        raise ValueError(
            f"option '{title or '?'}' sem o campo \"fact\" (chaves recebidas: {chaves}). "
            "Toda option precisa de \"title\" e \"fact\" preenchidos."
        )
    if not title:
        title = _titulo_do_fato(fact)
    fontes = _filtrar_fontes(opt.get("sources") or opt.get("fontes") or [], permitidas)
    if not fontes:
        raise ValueError(f"option '{title}' ficou sem fonte válida (url precisa ter vindo dos insumos)")
    risk, risk_bruto = _normalizar_risco(opt.get("risk") or opt.get("risco"))
    risk_note = sem_travessao((opt.get("risk_note") or opt.get("nota_risco") or "").strip())
    if risk is None:
        print(
            f"aviso: risk {risk_bruto!r} em '{title}' trocado por '{RISCO_PADRAO}'",
            file=sys.stderr,
        )
        risk = RISCO_PADRAO
        if not risk_note:
            risk_note = "Risco não informado pelo modelo; revisar antes de publicar."
    tl = opt.get("timeline") or {}
    if not isinstance(tl, dict):
        tl = {}
    rende = bool(tl.get("rende"))
    timeline = {"rende": rende}
    if rende:
        timeline["angle"] = sem_travessao((tl.get("angle") or "").strip())
        if not timeline["angle"]:
            raise ValueError(f"timeline.angle vazio em '{title}'")
    else:
        timeline["motivo"] = sem_travessao((tl.get("motivo") or tl.get("angle") or "").strip()) or "Não sustenta artigo agora."
    out = {
        "title": title,
        "tone": sem_travessao((opt.get("tone") or "").strip()),
        "fact": fact,
        "sources": fontes,
        "caption": sem_travessao((opt.get("caption") or "").strip()),
        "risk": risk,
        "risk_note": risk_note,
        "timeline": timeline,
    }
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
        out["duration"] = sem_travessao((opt.get("duration") or "").strip())
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
        out["slides"] = [sem_travessao(str(x)) for x in slides if str(x).strip()]
        if not (6 <= len(out["slides"]) <= 10):
            raise ValueError(f"carrossel '{title}' precisa de 6 a 10 slides (veio {len(out['slides'])})")
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
        out["poll"] = {
            "question": sem_travessao(str(poll.get("question")).strip()),
            "options": opcoes[:4],
        }
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


def normalizar_pauta(bruto, dia, gerado, slots_esperados, permitidas, excluidos_hoje):
    if not isinstance(bruto, dict):
        raise ValueError("o modelo não devolveu um objeto JSON")
    if TRAVESSAO_RE.search(json.dumps(bruto, ensure_ascii=False)):
        bruto = sem_travessao(bruto)
    slots_in = bruto.get("slots") or []
    if len(slots_in) != len(slots_esperados):
        raise ValueError(f"esperava {len(slots_esperados)} slots, veio {len(slots_in)}")
    slots = []
    for esp, s in zip(slots_esperados, slots_in):
        if not isinstance(s, dict):
            raise ValueError("slot inválido")
        tipo = s.get("type") or esp["type"]
        if tipo != esp["type"]:
            raise ValueError(f"slot type {tipo} != {esp['type']}")
        opts = s.get("options") or []
        if isinstance(opts, dict):
            opts = list(opts.values())
        if len(opts) != 2:
            raise ValueError(f"slot {esp['time']} precisa de 2 options, veio {len(opts)}")
        slots.append({
            "time": esp["time"],
            "type": esp["type"],
            "kind": sem_travessao((s.get("kind") or esp.get("kind") or "").strip()),
            "options": [_normalizar_option(o, tipo, permitidas) for o in opts],
        })
    doc = {
        "date": dia.isoformat(),
        "weekday": weekday_de(dia),
        "generated": gerado,
        "summary": sem_travessao((bruto.get("summary") or "").strip()),
        "agenda": limpar_agenda(bruto.get("agenda") or []),
        "slots": slots,
        "excluidos": excluidos_hoje or [],
    }
    if not doc["summary"]:
        raise ValueError("summary vazio")
    if TRAVESSAO_RE.search(json.dumps(doc, ensure_ascii=False)):
        raise ValueError("ainda há travessão no JSON")
    return doc


def atualizar_index(dia, pasta=None):
    pasta = Path(pasta or (ROOT / "pautas"))
    path = pasta / "index.json"
    if path.exists():
        ix = _ler_json(path)
    else:
        ix = {"days": []}
    days = list(ix.get("days") or [])
    chave = dia.isoformat() if hasattr(dia, "isoformat") else str(dia)
    if chave not in days:
        days.append(chave)
    days = sorted(set(days))
    path.write_text(json.dumps({"days": days}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _rodar_repeticao(arquivo):
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "repeticao.py"), str(arquivo)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    texto = (r.stdout or "") + (r.stderr or "")
    return r.returncode, texto


def _gravar(doc, arquivo):
    Path(arquivo).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _escrever_output(arquivo, dia):
    dest = os.environ.get("GITHUB_OUTPUT")
    if not dest:
        return
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write(f"arquivo={arquivo}\n")
        fh.write(f"data={dia.isoformat()}\n")


def gerar(dia=None, agora=None):
    agora = agora_sp(agora)
    dia = dia or data_da_pauta(agora)
    gerado = agora.replace(microsecond=0).isoformat()
    insumos = carregar_insumos(dia)
    permitidas = _urls_permitidas(
        insumos["noticias"], insumos["x_trends"], insumos.get("trends_x"),
        insumos["pautas_recentes"], insumos["sob"], insumos["quentes"],
    )
    print(
        f"pauta de {dia.isoformat()} ({weekday_de(dia)}), generated={gerado}, "
        f"edição={insumos.get('edicao_nome') or 'nenhuma'}, "
        f"notícias={len(insumos['noticias'])}, slots={len(insumos['slots'])}",
        file=sys.stderr,
    )
    arquivo = ROOT / "pautas" / f"{dia.isoformat()}.json"
    aviso = None
    ultimo_erro = None
    for tentativa in range(1, 4):
        mensagens = [
            {"role": "system", "content": _prompt_sistema(insumos.get("seguidores"))},
            {"role": "user", "content": _prompt_usuario(dia, gerado, insumos, aviso)},
        ]
        try:
            bruto = modelos_ia.completar(
                mensagens,
                schema_nome="pauta",
                max_tokens=8000,
                temperature=0.3,
                timeout=180,
            )
            doc = normalizar_pauta(
                bruto, dia, gerado, insumos["slots"], permitidas, insumos["excluidos_hoje"],
            )
        except (modelos_ia.ModelError, ValueError, json.JSONDecodeError) as e:
            ultimo_erro = e
            aviso = str(e)
            print(f"tentativa {tentativa} falhou: {e}", file=sys.stderr)
            continue
        _gravar(doc, arquivo)
        codigo, texto = _rodar_repeticao(arquivo)
        if codigo == 0:
            atualizar_index(dia)
            usados = ", ".join(modelos_ia.modelos_usados()) or "desconhecido"
            usd, _por = modelos_ia.custo_usd()
            print(f"pauta gravada em {arquivo} (modelo {usados}, ~US$ {usd:.4f})")
            print(texto.strip() or "sem repetição")
            _escrever_output(arquivo, dia)
            return arquivo
        ultimo_erro = RuntimeError(texto.strip() or "repetição")
        aviso = "O verificador de repetição recusou a pauta:\n" + texto
        print(f"tentativa {tentativa}: repetição\n{texto}", file=sys.stderr)
    raise SystemExit(f"não consegui gerar uma pauta válida: {ultimo_erro}")


def _self_test():
    # data da pauta
    d6 = agora_sp(datetime(2026, 11, 19, 5, 59, tzinfo=BRT))
    d7 = agora_sp(datetime(2026, 11, 19, 6, 0, tzinfo=BRT))
    assert data_da_pauta(d6).isoformat() == "2026-11-19", data_da_pauta(d6)
    assert data_da_pauta(d7).isoformat() == "2026-11-20", data_da_pauta(d7)
    assert weekday_de(data_da_pauta(d7)) == "Sex"

    # hora marcada, nunca data
    assert normalizar_hora("10h") == "10h"
    assert normalizar_hora("14h30") == "14h30"
    assert normalizar_hora("7h00") == "7h"
    assert normalizar_hora("14:30") == "14h30"
    assert normalizar_hora("11/11") is None
    assert normalizar_hora("8/11") is None
    assert normalizar_hora("8/10") is None
    assert normalizar_hora("11/11", "O prazo termina às 16h desta terça") == "16h"
    assert normalizar_hora("Noite") is None
    agenda = limpar_agenda([
        {"time": "10h", "text": "STF julga a lei.", "source": {"name": "Folha", "url": "https://folha.uol.com.br/x"}},
        {"time": "11/11", "text": "Vaquinha termina.", "source": {"name": "CNN", "url": "https://cnn.com/x"}},
        {"time": "8/11", "text": "A sessão começa às 14h30 no Senado.", "source": {"name": "GP", "url": "https://g.com/x"}},
    ])
    assert [a["time"] for a in agenda] == ["10h", "14h30"], agenda
    assert all(DATA_RE.match(a["time"]) is None for a in agenda)

    assert sem_travessao("um — dois – três") == "um - dois - três"

    # index sem duplicar
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pasta = Path(td)
        (pasta / "index.json").write_text('{"days": ["2026-11-09"]}\n', encoding="utf-8")
        atualizar_index(datetime(2026, 11, 20).date(), pasta=pasta)
        atualizar_index(datetime(2026, 11, 20).date(), pasta=pasta)
        ix = json.loads((pasta / "index.json").read_text(encoding="utf-8"))
        assert ix["days"] == ["2026-11-09", "2026-11-20"], ix

    # normalização recusa fonte inventada e aceita fonte do insumo
    permitidas = {"https://exemplo.com/materia"}
    slots = [{"time": "8h", "type": "r", "kind": "Reel teste"}]
    opt_r = {
        "title": "Senado aprova o ajuste fiscal do governo",
        "tone": "contundente",
        "fact": "O Senado aprovou o texto e enviou à Câmara.",
        "sources": [{"name": "Exemplo", "url": "https://exemplo.com/materia"}],
        "duration": "até 30s",
        "hooks": ["O Senado aprovou o ajuste.", "E agora vai para a Câmara?"],
        "script": "O Senado aprovou o ajuste fiscal. Você acha que a Câmara deve confirmar?",
        "screen_text": ["Senado aprovou", "Vai à Câmara"],
        "caption": "O Senado aprovou o ajuste. A Câmara deve confirmar, sim ou não?",
        "risk": "baixo",
        "risk_note": "Fato público da votação.",
        "timeline": {"rende": False, "motivo": "Votação pontual, sem fôlego de artigo."},
    }
    bruto = {
        "summary": "Quinta de votação no Congresso.",
        "agenda": [{"time": "10h", "text": "Sessão no Senado.", "source": {"name": "Exemplo", "url": "https://exemplo.com/materia"}}],
        "slots": [{"time": "8h", "type": "r", "kind": "Reel teste", "options": [opt_r, dict(opt_r, title="A Câmara ainda não votou o texto")]}],
    }
    doc = normalizar_pauta(bruto, datetime(2026, 11, 20).date(), "2026-11-19T20:00:00-03:00", slots, permitidas, [])
    assert doc["date"] == "2026-11-20"
    assert doc["agenda"][0]["time"] == "10h"
    assert len(doc["slots"][0]["options"]) == 2
    # chaves alternativas, título derivado do fato e risk vazio viram padrão
    alt = {k: v for k, v in opt_r.items() if k not in ("title", "fact", "risk")}
    alt["fato"] = "O Senado aprovou o ajuste fiscal. O texto vai à Câmara."
    alt["risk"] = ""
    o = _normalizar_option(alt, "r", permitidas)
    assert o["fact"].startswith("O Senado aprovou") and o["title"] == "O Senado aprovou o ajuste fiscal", o["title"]
    assert o["risk"] == "médio" and o["risk_note"] == "Fato público da votação.", o
    assert _normalizar_option(dict(opt_r, risk="Medium"), "r", permitidas)["risk"] == "médio"
    assert _normalizar_option({"A": opt_r}, "r", permitidas)["title"] == opt_r["title"]
    try:
        _normalizar_option({k: v for k, v in opt_r.items() if k != "fact"}, "r", permitidas)
        raise SystemExit("deveria recusar option sem fact")
    except ValueError as e:
        assert "fact" in str(e)
    b2 = json.loads(json.dumps(bruto))
    b2["slots"][0]["options"] = {"A": b2["slots"][0]["options"][0], "B": b2["slots"][0]["options"][1]}
    assert len(normalizar_pauta(b2, datetime(2026, 11, 20).date(), "2026-11-19T20:00:00-03:00", slots, permitidas, [])["slots"][0]["options"]) == 2

    try:
        ruim = json.loads(json.dumps(bruto))
        ruim["slots"][0]["options"][0]["sources"] = [{"name": "Fake", "url": "https://inventado.example/x"}]
        normalizar_pauta(ruim, datetime(2026, 11, 20).date(), "2026-11-19T20:00:00-03:00", slots, permitidas, [])
        raise SystemExit("deveria recusar url inventada")
    except ValueError as e:
        assert "fonte válida" in str(e)

    # week_plan
    plano = {"week_plan": [["Sex", [["r", "8h", "tema velho"], ["s", "Noite", "enquete"]]]]}
    sl = slots_do_plano(plano, datetime(2026, 11, 20).date())
    assert [s["type"] for s in sl] == ["r", "s"] and sl[0]["time"] == "8h"

    print("gerar_pauta: ok")


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        _self_test()
        return
    dia = None
    if "--data" in args:
        i = args.index("--data")
        try:
            dia = datetime.fromisoformat(args[i + 1]).date()
        except (IndexError, ValueError):
            print("uso: python3 tools/gerar_pauta.py [--data AAAA-MM-DD]", file=sys.stderr)
            raise SystemExit(2)
    gerar(dia=dia)


if __name__ == "__main__":
    main()

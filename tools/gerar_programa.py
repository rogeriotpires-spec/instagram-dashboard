"""Gera a pauta do programa Conversa Timeline com o cliente de IA das notícias."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ia_comum as C
import modelos_ia

ROOT = C.ROOT


def data_da_pauta(cfg, agora=None):
    agora = C.agora_sp(agora)
    virada = int(cfg.get("virada_hora") or 14)
    dia = agora.date()
    if agora.hour >= virada:
        dia = (agora + timedelta(days=1)).date()
    return dia


def _carregar_cfg():
    return C.ler_json(ROOT / "programa" / "config.json") or {}


def _anteriores(dia, n=3):
    out = []
    for k in range(1, 10):
        f = ROOT / "programa" / f"{(dia - timedelta(days=k)).isoformat()}.json"
        if not f.exists():
            continue
        d = C.ler_json(f)
        itens = []
        for it in d.get("itens") or []:
            itens.append({"titulo": it.get("titulo"), "resumo": (it.get("resumo") or "")[:240]})
        excl = [x.get("titulo") or x.get("title") for x in (d.get("excluidos") or [])]
        out.append({"date": d.get("date") or f.stem, "titulo": d.get("titulo"), "itens": itens, "excluidos": excl})
        if len(out) >= n:
            break
    return out


def _excluidos(dia, atual):
    titulos = []
    for x in (atual or {}).get("excluidos") or []:
        t = x.get("titulo") or x.get("title") or ""
        if t:
            titulos.append(t)
    for ant in _anteriores(dia, 3):
        titulos.extend(ant.get("excluidos") or [])
    vistos, out = set(), []
    for t in titulos:
        k = t.strip().lower()
        if k and k not in vistos:
            vistos.add(k)
            out.append(t)
    return out


def _avulsos(atual):
    return [it for it in (atual or {}).get("itens") or [] if it.get("avulsa") or it.get("pedido")]


SINAIS_ESCALA_NACIONAL = (
    "stf", "tse", "tcu", "pgr", "stj", "supremo", "congresso", "senado",
    "camara dos", "câmara dos", "planalto", "presidente lula", "governo lula",
    "governo federal", "ministerio", "ministério", "ipca", "selic", "inflacao",
    "inflação", "banco central", "policia federal", "polícia federal",
    "orcamento", "orçamento", "teto fiscal", "pacote fiscal", "imposto",
    "tarifa", "tribut", "contas publicas", "contas públicas", "dólar", "dolar",
    "em varios estados", "em vários estados", "brasil inteiro", "nacional",
    "cpi", "inquerito", "inquérito", "denuncia da pgr", "denúncia da pgr",
)
SINAIS_CLIMA_LOCAL = (
    "chuva", "temporal", "temporais", "alagamento", "deslizamento",
    "vendaval", "granizo", "amanhece sob",
)
SINAIS_CRIME_LOCAL = (
    "foragidos", "megaoperacao", "megaoperação", "homicidio", "homicídio",
    "assalto", "roubo de", "crimes patrimoniais",
)


def _blob_item(it):
    if not isinstance(it, dict):
        return ""
    partes = [it.get("titulo") or it.get("title") or "", it.get("subtitulo") or "",
              it.get("resumo") or it.get("summary") or "", it.get("bloco") or it.get("segment") or ""]
    return " ".join(partes)


def item_local_sem_escala(item):
    """Ocorrência restrita a um estado/cidade, sem repercussão nacional.

    Itens avulsos sempre passam. Julgamento no STF, pacote fiscal, IPCA e
    operação da PF não são locais só porque mencionam uma cidade.
    """
    if not isinstance(item, dict):
        return False
    if item.get("avulsa") or item.get("pedido"):
        return False
    blob = C.norm(_blob_item(item)) if hasattr(C, "norm") else _blob_item(item).lower()
    # fallback if ia_comum has no norm
    try:
        import noticias_notas as notas
        blob = notas.norm(_blob_item(item))
        tem = notas.contem_termo
        escala = any(tem(blob, s) for s in SINAIS_ESCALA_NACIONAL)
    except Exception:
        blob = _blob_item(item).lower()
        escala = any(s in blob for s in SINAIS_ESCALA_NACIONAL)
        def tem(texto, s):
            return s in texto
    if escala:
        return False
    bloco = (item.get("bloco") or item.get("segment") or "").strip().lower()
    if bloco in ("rio de janeiro", "rio"):
        return True
    if any(tem(blob, s) for s in SINAIS_CLIMA_LOCAL):
        return True
    if any(tem(blob, s) for s in SINAIS_CRIME_LOCAL) and any(
        tem(blob, p) for p in ("rio de janeiro", "no rio", "niteroi", "niterói", "baixada", "sao paulo", "são paulo")
    ):
        return True
    return False



def item_rejeitado_na_pauta(item):
    """Ocorrência local sem escala ou vitrine/agenda positiva do Planalto."""
    return item_local_sem_escala(item) or item_vitrine_governista(item)


def _texto_norm(item):
    raw = _blob_item(item)
    try:
        import noticias_notas as notas
        return notas.norm(raw)
    except Exception:
        return (raw or "").lower()


def item_vitrine_governista(item):
    """Enquadramento de vitrine ou agenda positiva do Planalto.

    Itens avulsos sempre passam. A função não descarta inflação, câmbio ou
    Fazenda em si; descarta o ângulo de boa notícia do governo: IPCA que
    "desacelera", dólar que cai porque o presidente sondou um nome,
    indicação ministerial como pauta positiva e protocolo identitário
    nas escolas.
    """
    if not isinstance(item, dict):
        return False
    if item.get("avulsa") or item.get("pedido"):
        return False
    blob = _texto_norm(item)
    try:
        import noticias_notas as notas
        titulo = notas.norm(item.get("titulo") or item.get("title") or "")
        tem = notas.contem_termo
    except Exception:
        titulo = (item.get("titulo") or item.get("title") or "").lower()

        def tem(texto, s):
            return s in (texto or "")

    if tem(blob, "racismo") and (
        tem(blob, "escola") or tem(blob, "escolas") or tem(blob, "protocolo")
    ):
        return True

    indica = (
        tem(blob, "indica") or tem(blob, "indicar") or tem(blob, "indicacao")
        or tem(blob, "demonstra intencao") or tem(blob, "intencao de indicar")
    )
    cargo = (
        tem(blob, "ministerio") or tem(blob, "seguranca publica")
    )
    if indica and cargo:
        return True

    dolar = tem(titulo, "dolar") or tem(blob, "dolar cai") or tem(blob, "dolar recua") or tem(titulo, "abaixo de")
    cai = tem(blob, "cai") or tem(blob, "recua") or tem(blob, "cai abaixo") or tem(blob, "abaixo de")
    governo_sonda = (
        tem(blob, "lula") or tem(blob, "sondar") or tem(blob, "sondou")
        or tem(blob, "trabuco") or tem(blob, "fazenda")
    )
    if dolar and cai and governo_sonda:
        return True

    if (tem(titulo, "desacelera") or tem(titulo, "desaceleracao")) and (
        tem(blob, "ipca") or tem(blob, "inflacao")
    ):
        return True
    return False


SINAIS_PRIORIDADE_OPOSICAO = (
    "teto", "ajuste fiscal", "despesas obrigatorias", "gasto publico",
    "contas publicas", "irregularidades fiscais", "pedalada",
    "cartao de credito", "rotativo", "endividamento", "limite do cartao",
    "moraes", "debora do batom", "prisao domiciliar",
    "liberdade de expressao", "perseguicao", "intimida", "seletividade",
    "relatorios ao stf", "ministros da corte", "segundo turno",
    "desvio de cota", "tcu", "cota parlamentar",
)


def peso_editorial(item):
    """Nota da edição mais bônus da linha de oposição; vitrine e local afundam."""
    if not isinstance(item, dict):
        return -100.0
    if item_rejeitado_na_pauta(item):
        return -100.0
    try:
        import noticias_notas as notas
        tem = notas.contem_termo
    except Exception:
        def tem(texto, s):
            return s in (texto or "")
    blob = _texto_norm(item)
    bonus = 0.0
    if any(tem(blob, s) for s in SINAIS_PRIORIDADE_OPOSICAO):
        bonus += 4.0
    if any(tem(blob, s) for s in (
        "teto", "ajuste fiscal", "irregularidades fiscais",
        "cartao de credito", "rotativo", "endividamento",
    )):
        bonus += 2.0
    if any(tem(blob, s) for s in (
        "moraes", "debora do batom", "seletividade", "intimida",
        "relatorios ao stf", "tcu",
    )):
        bonus += 2.0
    return float(item.get("score") or 0) + bonus


def priorizar_noticias(itens, excluir_rejeitados=True):
    vivos, evitados = [], []
    for it in itens or []:
        if not isinstance(it, dict):
            continue
        if excluir_rejeitados and item_rejeitado_na_pauta(it):
            evitados.append(it)
            continue
        vivos.append(it)
    vivos.sort(key=lambda x: peso_editorial(x), reverse=True)
    return vivos, evitados


def _mesmo_noticia(a, b):
    if a.get("id") and a.get("id") == b.get("id"):
        return True
    try:
        import noticias_notas as notas
        ta = (a.get("title") or a.get("titulo") or "") + " " + (a.get("summary") or a.get("resumo") or "")
        tb = (b.get("title") or b.get("titulo") or "") + " " + (b.get("summary") or b.get("resumo") or "")
        return notas.mesmo_assunto(ta, tb)
    except Exception:
        ta = (a.get("title") or a.get("titulo") or "").strip().lower()
        tb = (b.get("title") or b.get("titulo") or "").strip().lower()
        return bool(ta) and ta == tb


def _fundir_noticias(itens):
    saida = []
    for it in itens or []:
        achou = False
        for j, outro in enumerate(saida):
            if _mesmo_noticia(it, outro):
                if float(it.get("score") or 0) > float(outro.get("score") or 0):
                    saida[j] = it
                achou = True
                break
        if not achou:
            saida.append(it)
    return saida


def noticias_do_dia(dia):
    """Junta as edições do dia da pauta e prioriza a linha de oposição."""
    nomes, itens, trends, headline = [], [], [], ""
    for faixa in ("06h", "12h", "19h"):
        path = ROOT / "noticias" / f"{dia.isoformat()}-{faixa}.json"
        if not path.exists():
            continue
        ed = C.ler_json(path) or {}
        nomes.append(f"{dia.isoformat()}-{faixa}")
        parte, xt = C.resumir_noticias(ed, limite=40)
        itens.extend(parte)
        trends.extend(xt or [])
        if ed.get("headline"):
            headline = ed.get("headline")
    itens = _fundir_noticias(itens)
    priorizados, evitados = priorizar_noticias(itens)
    return nomes, priorizados, evitados, trends, headline


def marcar_destaque_avulso(item):
    if not isinstance(item, dict):
        return item
    if item.get("avulsa") or item.get("pedido"):
        out = dict(item)
        out["avulsa"] = True
        out["destaque"] = True
        return out
    return item


def _preservados(atual):
    """Avulsos primeiro; demais itens gerados, exceto os locais sem escala nacional."""
    avulsos = [marcar_destaque_avulso(it) for it in _avulsos(atual)]
    outros = []
    for it in (atual or {}).get("itens") or []:
        if it.get("avulsa") or it.get("pedido"):
            continue
        if item_rejeitado_na_pauta(it):
            motivo = "vitrine governista" if item_vitrine_governista(it) else "local sem escala nacional"
            print(f"item {motivo} deixado de fora: {it.get('titulo')}", file=sys.stderr)
            continue
        outros.append(it)
    return avulsos, outros


def _prompt_sistema(cfg, vagas=None, n_preservados=0, n_avulsos=0):
    voz = C.texto(ROOT / "prompts" / "voz-editorial.md")
    alvo = int(cfg.get("itens") or 6)
    n = alvo if vagas is None else int(vagas)
    frases = int(cfg.get("linhas_resumo") or 5)
    perguntas = int(cfg.get("perguntas_por_item") or 3)
    blocos = ", ".join(cfg.get("blocos") or [])
    linha = cfg.get("linha_editorial") or ""
    preserva = (
        f" Já existem {n_avulsos} item(ns) avulso(s) e {n_preservados} item(ns) aprovados na pauta. "
        "NÃO reescreva, troque, funda nem descarte esses itens. Não os inclua de novo em 'itens'. "
        "O script os preserva e coloca os avulsos no início, em destaque. "
        if (n_preservados or n_avulsos) else ""
    )
    vagas_txt = (
        f"itens: exatamente {n} itens NOVOS para completar as vagas (os {n_preservados} preservados e os avulsos não entram nesta lista). "
        if n else
        "itens: lista VAZIA ([]). Só reescreva titulo e abertura, incluindo os avulsos no começo. "
    )
    return f"""Você é o produtor de pauta do programa {cfg.get('programa') or 'Conversa Timeline'}, apresentado por Rogerio Pires (jornalista, professor e comentarista político no Rio, Revista Timeline e Jovem Pan News Rio). A pauta é um guia para a bancada: temas do dia, pesquisa básica, ângulo e perguntas.

Responda SOMENTE com um objeto JSON, sem markdown.

VOZ EDITORIAL:
{voz}

LINHA: {linha}
A Revista Timeline e o Conversa Timeline fazem oposição ao governo Lula e ao seu regime. Não invente dado; o enquadramento é firme e conservador.
{preserva}
A pauta é de relevância NACIONAL (Brasil inteiro). Não gaste vaga com ocorrência local restrita a um único estado (chuva local, crime local sem repercussão nacional), salvo se o assunto tiver escala nacional ou já estiver na pauta como item avulso. Se o insumo trouxer itens_locais_rejeitados, itens_vitrine_rejeitados ou noticias_evitar, não os recoloque nem recicle o enquadramento de vitrine.

SELEÇÃO E ENQUADRAMENTO (obrigatório):
A seleção NÃO copia a hierarquia nem a manchete da imprensa tradicional. Escolha os fatos do dia pelo interesse da oposição conservadora, nesta ordem:
1. contas públicas, teto, gastos, impostos, endividamento e impacto real no bolso (limites de crédito, inflação que pesa, pacote fiscal);
2. denúncia factual de irregularidade, impunidade, contradição ou escândalo do governo, da esquerda e de aliados;
3. abusos, seletividade e excessos do STF, da PGR e de outras instituições;
4. perseguição política, liberdade de expressão e segurança pública de escala nacional.

NÃO gaste vaga com vitrine ou agenda positiva do Planalto: indicador apresentado como desaceleração que favorece o governo, câmbio que cai porque o presidente sondou um nome, anúncio de indicação ministerial como pauta positiva, protocolo identitário nas escolas. Prefira o fato que fere o bolso, a credibilidade fiscal ou as instituições, não a manchete celebratória.

Quando o fato for denúncia da PGR ou do STF contra parlamentar da oposição, o título, o resumo, o ângulo e as perguntas DEVEM expor a seletividade da acusação e a versão do denunciado (intimidação, dois pesos e duas medidas). Não escreva como boletim da acusação nem como vitória do governo.

Títulos: manchete direta, sujeito e verbo, com o ângulo crítico e factual da oposição, ancorado só nas fontes. Nunca soem como release do Palácio do Planalto.

Priorize a leitura de {', '.join(cfg.get('fontes_preferidas') or [])}. Use as urls fornecidas; nunca invente link, número, data ou citação. O G1 não está disponível.

SAÍDA:
- date, generated (copie os valores informados), programa, titulo, abertura, agenda, itens.
- titulo: manchete afirmativa e direta juntando os 2 ou 3 fatos principais, começando pelos itens avulsos se houver. Sem pergunta retórica nem metáfora.
- abertura: 3 a 4 frases ligando os temas, começando pelos avulsos.
- agenda: só compromissos do DIA da pauta com hora marcada. Campo time é relógio (10h, 14h30), nunca data (11/11, 8/11). Pode ser vazia.
- {vagas_txt}Máximo 2 do mesmo bloco. Blocos: {blocos}.
- Cada item: id (curto, sem espaços), bloco, titulo (manchete direta, sujeito e verbo, até ~90 caracteres, ângulo da oposição, sem metáfora e sem tom de release do governo), subtitulo, resumo com EXATAMENTE {frases} frases na leitura conservadora, contexto (3 a 5 tópicos), angulo (2 a 4 tópicos assertivos, sem 'Análise do programa:'), perguntas ({perguntas} perguntas abertas), atencao (opcional), fontes (2 a 4 objetos {{name, url}} só com urls fornecidas).
- metafora: só em no máximo 25% dos itens, 1 a 2 frases, quando realmente esclarece; nos demais, omita o campo.
- Não repita tema das 3 pautas anteriores, dos avulsos, nem dos excluídos. Desdobramento novo de verdade: preencha "desdobramento".
- Um item por assunto. Português do Brasil. Nunca travessão (— ou –). Nunca afirme crime sem condenação.
"""


def _frases_resumo(texto):
    return re.findall(r"[^.!?]+[.!?]+", (texto or "").strip())


def ajustar_resumo(texto, frases):
    """Se o resumo passou da cota, fica só com as primeiras N frases. Não inventa texto."""
    t = C.sem_travessao((texto or "").strip())
    partes = _frases_resumo(t)
    if len(partes) > frases:
        return "".join(partes[:frases]).strip()
    return t


class ResumoForaDaRegra(ValueError):
    """Resumo que não deu para ajustar à cota de frases."""


def reescrever_resumo(item, frases, completar):
    """Pede ao modelo só o resumo daquele item, com exatamente N frases."""
    user = (
        f"Reescreva SOMENTE o campo resumo deste item para ter EXATAMENTE {frases} frases. "
        "Mantenha os fatos já escritos. Não invente número, data, citação nem fato novo. "
        "Responda só com JSON {\"resumo\": \"...\"}.\n\n"
        + json.dumps(
            {"titulo": item.get("titulo") or item.get("title"), "resumo": item.get("resumo"), "contexto": item.get("contexto")},
            ensure_ascii=False,
        )
    )
    bruto = completar(
        [
            {"role": "system", "content": "Você ajusta resumos de pauta. Responda só JSON com o campo resumo."},
            {"role": "user", "content": user},
        ],
        schema_nome="programa",
        max_tokens=2000,
        temperature=0.2,
        timeout=90,
    )
    resumo = ""
    if isinstance(bruto, dict):
        resumo = (bruto.get("resumo") or "").strip()
        if not resumo and isinstance(bruto.get("itens"), list) and bruto["itens"]:
            resumo = (bruto["itens"][0].get("resumo") or "").strip()
    if not resumo:
        raise ValueError("reescreita do resumo não devolveu texto")
    novo = dict(item)
    novo["resumo"] = resumo
    return novo


def _normalizar_item(it, cfg, permitidas):
    if not isinstance(it, dict):
        raise ValueError("item inválido")
    titulo = C.sem_travessao((it.get("titulo") or it.get("title") or "").strip())
    resumo = C.sem_travessao((it.get("resumo") or "").strip())
    if not titulo or not resumo:
        raise ValueError("item sem titulo ou resumo")
    frases = int(cfg.get("linhas_resumo") or 5)
    n = C.n_frases(resumo)
    if n != frases:
        ajustado = ajustar_resumo(resumo, frases)
        if C.n_frases(ajustado) == frases:
            print(f"resumo ajustado para {frases} frases: {titulo}", file=sys.stderr)
            resumo = ajustado
        else:
            raise ResumoForaDaRegra(f"resumo de '{titulo}' tem {C.n_frases(ajustado)} frases, precisava de {frases}")
    fontes = C.filtrar_fontes(it.get("fontes") or it.get("sources") or [], permitidas, minimo=1, maximo=4)
    if not fontes:
        raise ValueError(f"item '{titulo}' sem fonte válida")
    bloco = (it.get("bloco") or "").strip()
    if (cfg.get("blocos") or []) and bloco not in cfg["blocos"]:
        # aceita variação próxima; se vazio, cai no primeiro
        bloco = bloco or (cfg["blocos"][0])
    perguntas = [C.sem_travessao(str(x).strip()) for x in (it.get("perguntas") or []) if str(x).strip()]
    if len(perguntas) < int(cfg.get("perguntas_por_item") or 3):
        raise ValueError(f"item '{titulo}' sem perguntas suficientes")
    ident = re_id(it.get("id") or titulo)
    out = {
        "id": ident,
        "bloco": bloco,
        "titulo": titulo,
        "subtitulo": C.sem_travessao((it.get("subtitulo") or "").strip()),
        "resumo": resumo,
        "contexto": [C.sem_travessao(str(x).strip()) for x in (it.get("contexto") or []) if str(x).strip()][:6],
        "angulo": [C.sem_travessao(str(x).strip()) for x in (it.get("angulo") or []) if str(x).strip()][:4],
        "perguntas": perguntas[: int(cfg.get("perguntas_por_item") or 3) + 1],
        "fontes": fontes,
    }
    if it.get("metafora"):
        out["metafora"] = C.sem_travessao(str(it.get("metafora")).strip())
    if it.get("atencao"):
        out["atencao"] = C.sem_travessao(str(it.get("atencao")).strip())
    if it.get("desdobramento"):
        out["desdobramento"] = C.sem_travessao(str(it.get("desdobramento")).strip())
    if len(out["contexto"]) < 3 or len(out["angulo"]) < 2:
        raise ValueError(f"item '{titulo}' com contexto ou angulo curto demais")
    return out


def re_id(s):
    import unicodedata, re as _re
    # A IA às vezes devolve id numérico; normalize() só aceita str.
    t = unicodedata.normalize("NFD", str(s or ""))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = _re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return (t or "item")[:60]


def _titulo_item(it):
    if not isinstance(it, dict):
        return "?"
    return (it.get("titulo") or it.get("title") or "?").strip() or "?"


def normalizar(bruto, dia, gerado, cfg, permitidas, avulsos, excluidos, completar=None, preservados=None):
    if not isinstance(bruto, dict):
        raise ValueError("JSON inválido")
    frases = int(cfg.get("linhas_resumo") or 5)
    novos, descartados = [], []
    for it in (bruto.get("itens") or []):
        atual = it
        try:
            novos.append(_normalizar_item(atual, cfg, permitidas))
            continue
        except ResumoForaDaRegra as e:
            if completar is not None:
                try:
                    atual = reescrever_resumo(atual, frases, completar)
                    novos.append(_normalizar_item(atual, cfg, permitidas))
                    print(f"resumo reescrito para {frases} frases: {_titulo_item(it)}", file=sys.stderr)
                    continue
                except Exception as erro:
                    print(f"reescreita do resumo falhou: {_titulo_item(it)} ({erro})", file=sys.stderr)
                    e = erro if isinstance(erro, ValueError) else e
            titulo = _titulo_item(it)
            print(f"item descartado: {titulo} ({e})", file=sys.stderr)
            descartados.append({"titulo": titulo, "motivo": str(e)})
        except ValueError as e:
            raise
    filtrados = []
    for it in novos:
        if item_rejeitado_na_pauta(it):
            motivo = "vitrine governista" if item_vitrine_governista(it) else "local sem escala nacional"
            print(f"item novo {motivo} descartado: {it.get('titulo')}", file=sys.stderr)
            continue
        filtrados.append(it)
    novos = filtrados
    pres_ok = []
    for it in list(preservados or []):
        if item_rejeitado_na_pauta(it):
            motivo = "vitrine governista" if item_vitrine_governista(it) else "local sem escala nacional"
            print(f"item preservado {motivo} descartado: {it.get('titulo')}", file=sys.stderr)
            continue
        pres_ok.append(it)
    preservados = pres_ok
    alvo = int(cfg.get("itens") or 6)
    if not novos and not (preservados or avulsos):
        raise ValueError("nenhum item novo válido após ajuste e descarte")
    if novos and len(novos) != alvo:
        print(
            f"aviso: {len(novos)} itens novos, esperado {alvo}; sigo com os válidos",
            file=sys.stderr,
        )
    com_meta = sum(1 for it in novos if it.get("metafora"))
    teto_meta = max(1, len(novos) // 4 + 1)
    if com_meta > teto_meta:
        raise ValueError(f"metáfora em {com_meta} itens, acima do limite")
    # blocos: no máximo 2 do mesmo
    from collections import Counter
    cnt = Counter(it["bloco"] for it in novos)
    demais = [b for b, n in cnt.items() if n > 2]
    if demais:
        raise ValueError(f"mais de 2 itens no bloco {demais[0]}")
    avulsos = [marcar_destaque_avulso(it) for it in (avulsos or [])]
    itens = avulsos + list(preservados or []) + novos
    doc = {
        "date": dia.isoformat(),
        "generated": gerado,
        "programa": cfg.get("programa") or "Conversa Timeline",
        "titulo": C.sem_travessao((bruto.get("titulo") or "").strip()),
        "abertura": C.sem_travessao((bruto.get("abertura") or "").strip()),
        "agenda": C.limpar_agenda(bruto.get("agenda") or []),
        "itens": itens,
        "excluidos": excluidos if isinstance(excluidos, list) and excluidos and isinstance(excluidos[0], dict) else [{"titulo": t} for t in (excluidos or []) if isinstance(t, str)],
    }
    if descartados:
        doc["itens_descartados"] = descartados
    if not doc["titulo"] or not doc["abertura"]:
        raise ValueError("titulo ou abertura vazios")
    if C.tem_travessao(doc):
        raise ValueError("ainda há travessão no JSON")
    return doc


def _repeticao(arquivo):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "repeticao.py"), str(arquivo)], cwd=str(ROOT), capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def gerar(dia=None, agora=None):
    cfg = _carregar_cfg()
    agora = C.agora_sp(agora)
    dia = dia or data_da_pauta(cfg, agora)
    gerado = agora.replace(microsecond=0).isoformat()
    atual = C.ler_json(ROOT / "programa" / f"{dia.isoformat()}.json")
    refazer = "--refazer" in sys.argv
    avulsos, preservados = _preservados(atual)
    if refazer:
        preservados = []
        print("refazer: itens não avulsos serão gerados de novo; avulsos permanecem", file=sys.stderr)
    excluidos = _excluidos(dia, atual)
    ed_nome, edicao = C.edicao_recente()
    nomes, noticias, evitados, x_trends, headline_dia = noticias_do_dia(dia)
    if not noticias and not evitados and edicao:
        noticias, x_trends = C.resumir_noticias(edicao)
        noticias, evitados = priorizar_noticias(noticias)
        nomes = [ed_nome] if ed_nome else []
        headline_dia = (edicao or {}).get("headline") or ""
    permitidas = C.urls_de(noticias, evitados, x_trends, C.trends_arquivo(), avulsos, preservados)
    alvo = int(cfg.get("itens") or 6)
    vagas = max(0, alvo - len(preservados))
    rejeitados_atual = [
        it for it in (atual or {}).get("itens") or []
        if not (it.get("avulsa") or it.get("pedido")) and item_rejeitado_na_pauta(it)
    ]
    payload = {
        "date": dia.isoformat(),
        "generated": gerado,
        "itens_novos_exigidos": vagas,
        "avulsos_preservados": [{"titulo": a.get("titulo"), "pedido": a.get("pedido")} for a in avulsos],
        "itens_aprovados_preservados": [{"titulo": a.get("titulo"), "bloco": a.get("bloco")} for a in preservados],
        "itens_locais_rejeitados": [
            {"titulo": it.get("titulo")}
            for it in rejeitados_atual if item_local_sem_escala(it)
        ],
        "itens_vitrine_rejeitados": [
            {"titulo": it.get("titulo")}
            for it in rejeitados_atual if item_vitrine_governista(it)
        ] + [
            {"titulo": it.get("title") or it.get("titulo")}
            for it in evitados if item_vitrine_governista(it)
        ],
        "noticias_evitar": [
            {"title": it.get("title") or it.get("titulo"), "motivo": "vitrine governista ou ocorrência local"}
            for it in evitados
        ],
        "prioridade": "linha de oposição conservadora: contas públicas, gasto, imposto, impacto no bolso, abusos do STF/PGR, escândalos e contradições do governo e da esquerda, segurança e liberdade de expressão. Não gaste vaga com vitrine ou agenda positiva do Planalto",
        "edicao_noticias": ", ".join(nomes) or ed_nome,
        "headline_noticias": headline_dia or (edicao or {}).get("headline"),
        "noticias": noticias,
        "x_trends": x_trends,
        "trends_x": C.trends_arquivo(),
        "pautas_anteriores": _anteriores(dia),
        "excluidos": [x.get("titulo") if isinstance(x, dict) else x for x in excluidos],
    }
    aviso = None
    arquivo = ROOT / "programa" / f"{dia.isoformat()}.json"
    ultimo = None
    for tentativa in range(1, 4):
        user = "Monte a pauta do dia na linha de oposição conservadora. Só use fatos e urls dos insumos. Não invente fonte. Não copie manchete de vitrine do governo.\n\n" + json.dumps(payload, ensure_ascii=False)
        if aviso:
            user += "\n\nCORREÇÃO: a tentativa anterior falhou. Reescreva os itens novos corrigindo:\n" + aviso
        try:
            bruto = modelos_ia.completar(
                [{"role": "system", "content": _prompt_sistema(cfg, vagas=vagas, n_preservados=len(preservados), n_avulsos=len(avulsos))}, {"role": "user", "content": user}],
                schema_nome="programa",
                max_tokens=8000,
                temperature=0.3,
                timeout=180,
            )
            # excluidos no arquivo: preservar lista original de dicts
            excl_doc = (atual or {}).get("excluidos") or [{"titulo": t} for t in excluidos if isinstance(t, str)]
            doc = normalizar(bruto, dia, gerado, cfg, permitidas, avulsos, excl_doc, completar=modelos_ia.completar, preservados=preservados)
        except (modelos_ia.ModelError, ValueError, json.JSONDecodeError) as e:
            ultimo = e
            aviso = str(e)
            print(f"tentativa {tentativa} falhou: {e}", file=sys.stderr)
            continue
        C.gravar_json(arquivo, doc)
        codigo, texto = _repeticao(arquivo)
        if codigo == 0:
            C.atualizar_index(ROOT / "programa", dia)
            print(f"programa gravado em {arquivo}")
            print(texto.strip() or "sem repetição")
            C.escrever_output(arquivo=str(arquivo), data=dia.isoformat(), changed="1")
            return arquivo
        ultimo = RuntimeError(texto.strip() or "repetição")
        aviso = "O verificador de repetição recusou:\n" + texto
        print(f"tentativa {tentativa}: repetição\n{texto}", file=sys.stderr)
    raise SystemExit(f"não consegui gerar a pauta do programa: {ultimo}")


def _item_base(titulo, resumo, ident="item"):
    return {
        "id": ident,
        "bloco": "Economia",
        "titulo": titulo,
        "subtitulo": "Texto segue para a Câmara",
        "resumo": resumo,
        "contexto": ["Fato 1 com número.", "Fato 2 com data.", "Fato 3."],
        "angulo": ["O programa defende X.", "Isso pesa no bolso."],
        "perguntas": ["P1?", "P2?", "P3?"],
        "fontes": [{"name": "Ex", "url": "https://ex.com/a"}],
    }


def _self_test():
    cfg = {"itens": 6, "linhas_resumo": 5, "perguntas_por_item": 3, "blocos": ["Economia", "Política", "Segurança", "Esportes"], "virada_hora": 14}
    d13 = data_da_pauta(cfg, datetime(2026, 11, 19, 13, 59, tzinfo=C.BRT))
    d14 = data_da_pauta(cfg, datetime(2026, 11, 19, 14, 0, tzinfo=C.BRT))
    assert d13.isoformat() == "2026-11-19", d13
    assert d14.isoformat() == "2026-11-20", d14
    assert re_id("PF afasta delegados!") == "pf-afasta-delegados"
    assert re_id(123) == "123"
    assert re_id(None) == "item"
    permitidas = {"https://ex.com/a"}
    it = _item_base(
        "Senado aprova o ajuste fiscal do governo",
        "O Senado aprovou o ajuste. O impacto caiu. O texto vai à Câmara. A oposição ficou isolada. O governo celebra o acordo.",
        "ajuste-fiscal",
    )
    n = _normalizar_item(it, cfg, permitidas)
    assert n["titulo"].startswith("Senado")

    # Caso real da pauta 25/11, tentativa com 6 frases: ajusta e segue.
    seis = (
        "Casos de violência e feminicídio voltam à pauta nacional. "
        "O Ministério Público denunciou um ex-boxeador em Santa Catarina. "
        "Um idoso foi preso no Paraná sob suspeita de estupro. "
        "Um professor foi preso na Paraíba. "
        "A polícia prendeu um suspeito em São Gonçalo. "
        "O tema segue sob investigação em vários estados."
    )
    it6 = _item_base("Casos de violência e feminicídio voltam à pauta nacional", seis, "violencia-6")
    assert C.n_frases(it6["resumo"]) == 6
    n6 = _normalizar_item(it6, cfg, permitidas)
    assert C.n_frases(n6["resumo"]) == 5
    assert "vários estados" not in n6["resumo"]

    # Caso real da pauta 25/11, título da edição das 06h com 7 frases.
    sete = (
        "Diversos casos de segurança pública e violência são registrados no Brasil. "
        "O Ministério Público denunciou um ex-boxeador em Santa Catarina. "
        "Um idoso de 84 anos foi preso no Paraná. "
        "Um professor foi preso na Paraíba. "
        "A polícia prendeu um suspeito em São Gonçalo. "
        "A novela da TV Globo entrou no mesmo resumo por engano. "
        "O agrupamento misturou fatos que deveriam ficar separados."
    )
    it7 = _item_base(
        "Diversos casos de segurança pública e violência são registrados no Brasil",
        sete,
        "violencia-7",
    )
    assert C.n_frases(it7["resumo"]) == 7
    n7 = _normalizar_item(it7, cfg, permitidas)
    assert C.n_frases(n7["resumo"]) == 5
    assert "misturou fatos" not in n7["resumo"]

    # Resumo curto demais não se ajusta: descarta só aquele item e publica o resto.
    blocos = ["Economia", "Economia", "Política", "Política", "Segurança"]
    bons = []
    for i, bloco in enumerate(blocos):
        it = _item_base(
            f"Senado aprova o ajuste fiscal do ponto {i+1}",
            "O Senado aprovou o ajuste. O impacto caiu. O texto vai à Câmara. A oposição ficou isolada. O governo celebra o acordo.",
            f"ajuste-{i+1}",
        )
        it["bloco"] = bloco
        bons.append(it)
    ruim = _item_base(
        "Resumo incompleto da pauta de teste",
        "Só duas frases saíram. Faltou o restante da pesquisa.",
        "resumo-curto",
    )
    bruto = {
        "titulo": "Pauta de teste com um item inválido",
        "abertura": "A bancada começa pelo ajuste. Depois vê o item que falhou. O restante da pauta segue.",
        "agenda": [],
        "itens": bons + [ruim],
    }
    import io
    buf = io.StringIO()
    antigo = sys.stderr
    sys.stderr = buf
    try:
        doc = normalizar(bruto, datetime(2026, 11, 25).date(), "2026-11-25T09:15:00-03:00", cfg, permitidas, [], [])
    finally:
        sys.stderr = antigo
    log = buf.getvalue()
    assert "item descartado: Resumo incompleto da pauta de teste" in log, log
    assert len([x for x in doc["itens"] if not x.get("avulsa")]) == 5
    assert doc["itens_descartados"][0]["titulo"] == "Resumo incompleto da pauta de teste"

    # Se todos os novos caem, a normalização ainda falha para o gerar() tentar de novo.
    bruto_vazio = {"titulo": "Pauta sem item válido", "abertura": "Nada passou. A pauta não fecha. Falta matéria.", "itens": [ruim]}
    try:
        normalizar(bruto_vazio, datetime(2026, 11, 25).date(), "2026-11-25T09:15:00-03:00", cfg, permitidas, [], [])
        raise AssertionError("deveria falhar quando não resta item novo")
    except ValueError as e:
        assert "nenhum item novo válido" in str(e)

    # Preserva itens aprovados, dá destaque ao avulso e barra pauta só local.
    chuva = {
        "id": "chuva-rio", "bloco": "Rio de Janeiro",
        "titulo": "Rio amanhece sob chuva forte com previsão de novos temporais",
        "resumo": "O Rio de Janeiro amanheceu sob chuva. Houve alagamentos na capital e na Baixada.",
    }
    foragidos = {
        "id": "foragidos-rio", "bloco": "Segurança pública",
        "titulo": "Polícia Civil prende 423 foragidos em megaoperação contra crimes patrimoniais no Rio",
        "resumo": "A Polícia Civil do Rio de Janeiro prendeu 423 foragidos. A ação foca crimes patrimoniais no estado.",
    }
    ipca = _item_base(
        "IPCA-15 sobe 0,22% em novembro e desacelera",
        "O IPCA-15 subiu 0,22% em novembro. A taxa ficou abaixo da do mês anterior. A queda dos combustíveis puxou a desaceleração. Os alimentos subiram. O IBGE divulgou os dados.",
        "ipca-15",
    )
    avulsa = dict(_item_base(
        "Advogada que acusou Janones de estupro relata ameaças e ataques nas redes",
        "A advogada relatou ameaças. O deputado é réu confesso de rachadinha. O caso ganhou repercussão nacional. A equipe dela registrou os ataques. O processo segue em apuração.",
        "janones-ameacas",
    ), avulsa=True, pedido="Janones denúncia estupro ameaças", bloco="Política nacional")
    assert item_local_sem_escala(chuva) and item_local_sem_escala(foragidos)
    assert not item_local_sem_escala(ipca)
    assert not item_local_sem_escala(avulsa)
    assert not item_local_sem_escala({
        "titulo": "STF marca para 4 de dezembro o julgamento sobre segundo turno no Rio de Janeiro",
        "resumo": "O Supremo Tribunal Federal pautou o julgamento.",
        "bloco": "Rio de Janeiro",
    })

    # Calibração do Rogerio (26/11): itens 2-5 são vitrine; 6 e 7 fazem sentido;
    # o avulso do Janones sempre passa.
    dolar = {
        "titulo": "Dólar cai abaixo de R$ 5,80 após Lula sondar Luiz Trabuco para Fazenda",
        "resumo": "O dólar recuou depois que o presidente sondou o banqueiro para o ministério.",
    }
    alckmin = {
        "titulo": "Lula demonstra intenção de indicar Alckmin para Ministério da Segurança Pública",
        "resumo": "O presidente demonstra intenção de indicar o vice para o Ministério da Segurança Pública.",
    }
    escolas = {
        "titulo": "Câmara aprova protocolo nacional para combater racismo nas escolas",
        "resumo": "A Câmara aprovou o protocolo nacional contra o racismo nas escolas.",
        "bloco": "Educação",
    }
    cartao = _item_base(
        "Banco Central reduz limite do cartão de crédito para usuários frequentes do rotativo",
        "O Banco Central reduz o limite do cartão de quem usa o rotativo com frequência. A regra atinge famílias endividadas. O crédito no bolso fica mais curto. A decisão veio do CMN. Os bancos terão de aplicar o limite.",
        "bc-cartao",
    )
    cartao["bloco"] = "Economia"
    gayer = _item_base(
        "PGR denuncia Gustavo Gayer por suposto desvio de cota parlamentar",
        "A PGR denunciou o deputado por suposto desvio de cota. Gayer classifica o ato como tentativa de intimidá-lo. O caso menciona o ministro Moraes. A defesa aponta seletividade. O processo segue no Supremo.",
        "pgr-gayer",
    )
    gayer["bloco"] = "Política"
    assert item_vitrine_governista(ipca)
    assert item_vitrine_governista(dolar)
    assert item_vitrine_governista(alckmin)
    assert item_vitrine_governista(escolas)
    assert not item_vitrine_governista(cartao)
    assert not item_vitrine_governista(gayer)
    assert not item_vitrine_governista(avulsa)
    assert not item_vitrine_governista({
        "titulo": "TCU afasta multas a Dilma Rousseff e auxiliares por irregularidades fiscais de 2014",
        "resumo": "O TCU afastou as multas ligadas às irregularidades fiscais de 2014.",
    })

    atual = {"itens": [avulsa, ipca, dolar, alckmin, escolas, cartao, gayer, chuva, foragidos]}
    avs, pres = _preservados(atual)
    assert len(avs) == 1 and avs[0]["destaque"] is True and avs[0]["avulsa"] is True
    tit_pres = [x["titulo"] for x in pres]
    assert cartao["titulo"] in tit_pres
    assert gayer["titulo"] in tit_pres
    assert ipca["titulo"] not in tit_pres
    assert dolar["titulo"] not in tit_pres
    assert alckmin["titulo"] not in tit_pres
    assert escolas["titulo"] not in tit_pres

    candidatos = [
        {"title": ipca["titulo"], "summary": ipca["resumo"], "score": 8.3, "segment": "economia"},
        {"title": dolar["titulo"], "summary": dolar.get("resumo", ""), "score": 7.1, "segment": "economia"},
        {"title": alckmin["titulo"], "summary": alckmin.get("resumo", ""), "score": 6.8, "segment": "politica"},
        {"title": escolas["titulo"], "summary": escolas.get("resumo", ""), "score": 5.8, "segment": "educacao"},
        {"title": cartao["titulo"], "summary": cartao["resumo"], "score": 6.9, "segment": "economia"},
        {"title": gayer["titulo"], "summary": gayer["resumo"], "score": 6.0, "segment": "politica"},
        {"title": "TCU afasta multas a Dilma Rousseff e auxiliares por irregularidades fiscais de 2014",
         "summary": "O TCU afastou as multas por irregularidades fiscais de 2014.", "score": 8.1, "segment": "politica"},
        {"title": "Equipe econômica avalia teto individualizado de 2,5% para despesas obrigatórias em novo pacote de ajuste fiscal",
         "summary": "A equipe econômica discute um teto de 2,5% para as despesas obrigatórias.", "score": 6.8, "segment": "economia"},
        {"title": "Rio amanhece sob chuva forte com previsão de novos temporais",
         "summary": "O Rio de Janeiro amanheceu sob chuva. Houve alagamentos na capital.", "score": 8.3, "segment": "rio"},
    ]
    priorizados, evitados = priorizar_noticias(candidatos)
    ev_t = " ".join((x.get("title") or "") for x in evitados).lower()
    pr_t = " ".join((x.get("title") or "") for x in priorizados)
    assert "desacelera" in ev_t
    assert "dólar cai" in ev_t or "dolar cai" in ev_t.replace("ó", "o")
    assert "alckmin" in ev_t
    assert "racismo nas escolas" in ev_t
    assert "chuva" in ev_t
    assert "cartão de crédito" in pr_t or "cartao de credito" in pr_t.lower()
    assert "Gayer" in pr_t
    assert "Dilma" in pr_t
    assert "teto" in pr_t.lower()
    assert all(not item_vitrine_governista(x) for x in priorizados)
    assert peso_editorial(cartao) > peso_editorial(ipca)
    assert peso_editorial(gayer) > peso_editorial(alckmin)

    # inclusão avulsa / regeneração: itens aprovados de oposição permanecem; vitrine sai
    novo = _item_base(
        "TCU afasta multas a Dilma Rousseff por irregularidades fiscais",
        "O TCU afastou as multas. A decisão alcança auxiliares de 2014. A irregularidade fiscal voltou à pauta. A oposição cobra responsabilidade. O caso segue no tribunal.",
        "tcu-dilma",
    )
    novo["bloco"] = "Política"
    bruto = {
        "titulo": "Janones, o cartão no rotativo e o TCU",
        "abertura": "A pauta abre pela denúncia envolvendo Janones. O Banco Central aperta o cartão. A bancada também vê o TCU.",
        "agenda": [],
        "itens": [novo],
    }
    doc = normalizar(
        bruto, datetime(2026, 11, 26).date(), "2026-11-26T10:30:00-03:00",
        cfg, permitidas, [avulsa], [], preservados=[cartao, ipca],
    )
    titulos = [x["titulo"] for x in doc["itens"]]
    assert titulos[0].startswith("Advogada que acusou Janones")
    assert doc["itens"][0].get("destaque") is True
    assert cartao["titulo"] in titulos
    assert ipca["titulo"] not in titulos
    assert "TCU afasta" in titulos[2]
    assert "chuva" not in " ".join(titulos).lower()
    assert "foragidos" not in " ".join(titulos).lower()
    local_novo = _item_base(
        "Polícia Civil prende 423 foragidos em megaoperação contra crimes patrimoniais no Rio",
        "A Polícia Civil do Rio de Janeiro prendeu 423 criminosos. A ação foca crimes patrimoniais no estado. A operação ocorreu na capital. Os moradores acompanham o resultado. A secretaria estadual coordenou a ação.",
        "foragidos-rio",
    )
    local_novo["bloco"] = "Segurança"
    vitrine_nova = _item_base(
        "IPCA-15 sobe 0,22% em novembro e desacelera",
        "O IPCA-15 subiu 0,22% em novembro. A taxa ficou abaixo da do mês anterior. A queda dos combustíveis puxou a desaceleração. Os alimentos subiram. O IBGE divulgou os dados.",
        "ipca-15-novo",
    )
    vitrine_nova["bloco"] = "Economia"
    bruto2 = {
        "titulo": "Janones e o cartão na pauta nacional",
        "abertura": "A pauta abre pelo avulso. O Banco Central aperta o crédito. A bancada segue nos temas nacionais.",
        "agenda": [],
        "itens": [novo, local_novo, vitrine_nova],
    }
    doc2 = normalizar(
        bruto2, datetime(2026, 11, 26).date(), "2026-11-26T10:30:00-03:00",
        cfg, permitidas, [avulsa], [], preservados=[cartao],
    )
    t2 = " ".join(x["titulo"] for x in doc2["itens"]).lower()
    assert "foragidos" not in t2 and "chuva" not in t2, t2
    assert "desacelera" not in t2
    assert any("Janones" in x["titulo"] for x in doc2["itens"])
    assert any("cartão de crédito" in x["titulo"] or "cartao" in x["titulo"].lower() for x in doc2["itens"])
    assert any("TCU afasta" in x["titulo"] for x in doc2["itens"])
    texto_prompt = _prompt_sistema(cfg, vagas=2, n_preservados=1, n_avulsos=1)
    assert "oposição ao governo Lula" in texto_prompt
    assert "relevância NACIONAL" in texto_prompt
    assert "NÃO reescreva" in texto_prompt
    assert "SELEÇÃO E ENQUADRAMENTO" in texto_prompt
    assert "vitrine ou agenda positiva do Planalto" in texto_prompt
    assert "seletividade da acusação" in texto_prompt

    # Edições reais do dia, se estiverem no repositório: vitrine fora, 6 e 7 dentro.
    nomes_dia, priorizados_dia, evitados_dia, _, _ = noticias_do_dia(datetime(2026, 11, 26).date())
    if priorizados_dia or evitados_dia:
        evd = " ".join((x.get("title") or "") for x in evitados_dia).lower()
        prd = " ".join((x.get("title") or "") for x in priorizados_dia)
        assert "desacelera" in evd
        assert "alckmin" in evd
        assert "racismo nas escolas" in evd
        assert "Gayer" in prd
        assert "cartão de crédito" in prd or "rotativo" in prd.lower() or "endividamento" in prd.lower()
        assert "Dilma" in prd or "teto" in prd.lower() or "Débora" in prd or "Moraes" in prd

    print("gerar_programa: ok")


def main():
    if "--self-test" in sys.argv:
        _self_test()
        return
    dia = None
    if "--data" in sys.argv:
        i = sys.argv.index("--data")
        dia = datetime.fromisoformat(sys.argv[i + 1]).date()
    gerar(dia=dia)


if __name__ == "__main__":
    main()

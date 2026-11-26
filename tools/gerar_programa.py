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
        if item_local_sem_escala(it):
            print(f"item local sem escala nacional deixado de fora: {it.get('titulo')}", file=sys.stderr)
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
A Revista Timeline e o Conversa Timeline fazem oposição ao governo Lula e ao seu regime. Priorize economia (inflação, gastos públicos, impostos, contas públicas, impacto no bolso do brasileiro) e a denúncia factual de abusos, erros e absurdos do governo e das instituições. Não invente dado; o enquadramento é firme e conservador.
{preserva}
A pauta é de relevância NACIONAL (Brasil inteiro). Não gaste vaga com ocorrência local restrita a um único estado (chuva local, crime local sem repercussão nacional), salvo se o assunto tiver escala nacional ou já estiver na pauta como item avulso. Se o insumo trouxer itens_locais_rejeitados, não os recoloque.
Priorize a leitura de {', '.join(cfg.get('fontes_preferidas') or [])}. Use as urls fornecidas; nunca invente link, número, data ou citação. O G1 não está disponível.

SAÍDA:
- date, generated (copie os valores informados), programa, titulo, abertura, agenda, itens.
- titulo: manchete afirmativa e direta juntando os 2 ou 3 fatos principais, começando pelos itens avulsos se houver. Sem pergunta retórica nem metáfora.
- abertura: 3 a 4 frases ligando os temas, começando pelos avulsos.
- agenda: só compromissos do DIA da pauta com hora marcada. Campo time é relógio (10h, 14h30), nunca data (11/11, 8/11). Pode ser vazia.
- {vagas_txt}Máximo 2 do mesmo bloco. Blocos: {blocos}.
- Cada item: id (curto, sem espaços), bloco, titulo (manchete direta, sujeito e verbo, até ~90 caracteres, sem metáfora), subtitulo, resumo com EXATAMENTE {frases} frases na leitura conservadora, contexto (3 a 5 tópicos), angulo (2 a 4 tópicos assertivos, sem 'Análise do programa:'), perguntas ({perguntas} perguntas abertas), atencao (opcional), fontes (2 a 4 objetos {{name, url}} só com urls fornecidas).
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
        if item_local_sem_escala(it):
            print(f"item novo local sem escala nacional descartado: {it.get('titulo')}", file=sys.stderr)
            continue
        filtrados.append(it)
    novos = filtrados
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
    noticias, x_trends = C.resumir_noticias(edicao)
    permitidas = C.urls_de(noticias, x_trends, C.trends_arquivo(), avulsos, preservados)
    alvo = int(cfg.get("itens") or 6)
    vagas = max(0, alvo - len(preservados))
    payload = {
        "date": dia.isoformat(),
        "generated": gerado,
        "itens_novos_exigidos": vagas,
        "avulsos_preservados": [{"titulo": a.get("titulo"), "pedido": a.get("pedido")} for a in avulsos],
        "itens_aprovados_preservados": [{"titulo": a.get("titulo"), "bloco": a.get("bloco")} for a in preservados],
        "itens_locais_rejeitados": [
            {"titulo": it.get("titulo")}
            for it in (atual or {}).get("itens") or []
            if not (it.get("avulsa") or it.get("pedido")) and item_local_sem_escala(it)
        ],
        "prioridade": "relevância nacional (Brasil inteiro), economia, política nacional, Judiciário e denúncia factual de abusos do governo Lula; não pautar ocorrência local de um só estado",
        "edicao_noticias": ed_nome,
        "headline_noticias": (edicao or {}).get("headline"),
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
        user = "Monte a pauta do dia. Só use fatos e urls dos insumos. Não invente fonte.\n\n" + json.dumps(payload, ensure_ascii=False)
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
    atual = {"itens": [avulsa, ipca, chuva, foragidos]}
    avs, pres = _preservados(atual)
    assert len(avs) == 1 and avs[0]["destaque"] is True and avs[0]["avulsa"] is True
    assert [x["titulo"] for x in pres] == [ipca["titulo"]]
    # inclusão avulsa / regeneração: itens aprovados permanecem; avulso abre a pauta
    bruto = {
        "titulo": "Janones, IPCA e o ajuste do governo",
        "abertura": "A pauta abre pela denúncia envolvendo Janones. O IPCA-15 desacelera. A bancada também vê o item novo do governo.",
        "agenda": [],
        "itens": [dict(it, titulo="Senado aprova o ajuste fiscal do ponto extra") for it in [ipca]],
    }
    # reuse a valid new item
    novo = _item_base(
        "TCU afasta multas a Dilma Rousseff por irregularidades fiscais",
        "O TCU afastou as multas. A decisão alcança auxiliares de 2014. A irregularidade fiscal voltou à pauta. A oposição cobra responsabilidade. O caso segue no tribunal.",
        "tcu-dilma",
    )
    novo["bloco"] = "Política"
    bruto["itens"] = [novo]
    doc = normalizar(
        bruto, datetime(2026, 11, 26).date(), "2026-11-26T10:30:00-03:00",
        cfg, permitidas, [avulsa], [], preservados=[ipca],
    )
    titulos = [x["titulo"] for x in doc["itens"]]
    assert titulos[0].startswith("Advogada que acusou Janones")
    assert doc["itens"][0].get("destaque") is True
    assert ipca["titulo"] in titulos
    assert "TCU afasta" in titulos[2]
    assert "chuva" not in " ".join(titulos).lower()
    assert "foragidos" not in " ".join(titulos).lower()
    local_novo = _item_base(
        "Polícia Civil prende 423 foragidos em megaoperação contra crimes patrimoniais no Rio",
        "A Polícia Civil do Rio de Janeiro prendeu 423 criminosos. A ação foca crimes patrimoniais no estado. A operação ocorreu na capital. Os moradores acompanham o resultado. A secretaria estadual coordenou a ação.",
        "foragidos-rio",
    )
    local_novo["bloco"] = "Segurança"
    bruto2 = {
        "titulo": "Janones e o IPCA na pauta nacional",
        "abertura": "A pauta abre pelo avulso. O IPCA-15 desacelera. A bancada segue nos temas nacionais.",
        "agenda": [],
        "itens": [novo, local_novo],
    }
    doc2 = normalizar(
        bruto2, datetime(2026, 11, 26).date(), "2026-11-26T10:30:00-03:00",
        cfg, permitidas, [avulsa], [], preservados=[ipca],
    )
    t2 = " ".join(x["titulo"] for x in doc2["itens"]).lower()
    assert "foragidos" not in t2 and "chuva" not in t2, t2
    assert any("Janones" in x["titulo"] for x in doc2["itens"])
    assert any("IPCA-15" in x["titulo"] for x in doc2["itens"])
    assert any("TCU afasta" in x["titulo"] for x in doc2["itens"])
    texto_prompt = _prompt_sistema(cfg, vagas=2, n_preservados=1, n_avulsos=1)
    assert "oposição ao governo Lula" in texto_prompt
    assert "relevância NACIONAL" in texto_prompt
    assert "NÃO reescreva" in texto_prompt

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

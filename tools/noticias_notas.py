"""Notas, seção, assimetria e o que fica de fora da edição.

Usado pela redação com IA e pelo fallback de regras, para a conta não mudar
conforme o modo. A cobertura segue o critério do config (1 portal = 2, 3 = 5,
6 = 8, 9 ou mais = 10). Destaque e persistência não ficam cravados: o destaque
sai do tier dos portais e da quantidade; a persistência sai das edições recentes.
"""
import re
import unicodedata

DIREITA = {"direita", "centro-direita"}
ESQUERDA = {"esquerda", "centro-esquerda"}
GENERICOS = set("""governo brasil brasileira brasileiro presidente ministro federal nacional
publico estado cidade paulo janeiro hoje ontem agora novo nova anos ano dia dias
mil milhoes bilhao bilhoes reais dolar contra sobre apos depois ainda disse diz
afirma segundo pais mundo pessoas pessoa caso nova novo""".split())
ENTRETENIMENTO = (
    "james bond", "pattinson", "hollywood", "bbb", "big brother", "novela",
    "horoscopo", "signo de", "mega-sena", "mega sena", "lotofacil", "quina acumulada",
    "previsao do tempo", "receita de", "celebridade", "famosos", "netflix anuncia",
    "disney anuncia", "oscar", "reality", "grammy", "anitta", "cantada", "paquera",
    "flerte", "frevo", "album de musica", "sertanejo", "influencer", "van gogh", "sotheby",
    "unhas", "unha", "esmalte", "beleza", "moda",
)
# Editorias de lifestyle no endereço da matéria (O Globo Ela, novelas, horóscopo).
CAMINHOS_ENTRETENIMENTO = ("/ela/", "/play/novelas/", "/horoscopo/", "/horoscopo")
SINAIS_POLITICA = (
    "lula", "bolsonaro", "stf", "moraes", "congresso", "senado", "camara",
    "governo", "ministro", "eleicao", "eleicoes", "tse",     "pf", "policia federal",
    "pl", "pt", "stj", "inquerito", "prisao", "preso", "policia", "operacao",
)
PALAVRAS = {
    "educacao": ("enem", "escola", "escolas", "educacao", "aluno", "professor", "universidade",
                 "vestibular", "inep", "enade", "fies", "prouni", "mec", "faetec",
                 "uerj", "uenf", "cederj", "cecierj", "seeduc", "ideb", "saeb", "pisa"),
    "esportes": ("flamengo", "vasco", "fluminense", "botafogo", "palmeiras", "corinthians",
                 "futebol", "copa do brasil", "libertadores", "selecao brasileira",
                 "brasileirao", "cbf", "olimp", "volei", "formula 1", "tenis",
                 "uefa", "liga das nacoes", "copa do mundo", "champions", "ancelotti",
                 "eliminatorias", "amistoso", "atp", "wta"),
    "ciencia": ("inteligencia artificial", "tecnologia", "ciencia", "cientista", "pesquisa",
                "startup", "nvidia", "openai", "chatgpt", "nasa", "satelite", "foguete",
                "cnpq", "capes", "finep", "faperj", "vacina", "anvisa",
                "ararinha", "arara-azul", "zoologico", "fauna", "ibama",
                "biodiversidade", "especie ameacada", "extincao"),
    "rio": ("rio de janeiro", "baixada", "niteroi", "alerj", "duque de caxias",
            "prefeitura do rio", "governo do rio", "marica", "sao goncalo",
            "eduardo paes", "claudio castro", "camara do rio", "mare", "jacarepagua", "rj"),
    "seguranca": ("policia", "policial", "prisao", "preso", "trafico", "faccao",
                  "homicidio", "assassinato", "feminicidio", "operacao policial", "milicia",
                  "municao", "municoes", "pericia", "estupro", "explosivo"),
    "economia": ("inflacao", "selic", "juros", "dolar", "bolsa", "pib", "imposto", "economica",
                 "receita federal", "desemprego", "salario", "salarial", "abono", "beneficio",
                 "inss", "fgts", "banco central", "copom", "arcabouco", "fiscal"),
    "internacional": ("trump", "putin", "ucrania", "russia", "estados unidos", "china",
                      "israel", "ira", "iraque", "venezuela", "argentina", "onu", "otan", "california"),
}


STOP = set("""de da do das dos e em no na nos nas ao aos para por com sem que um uma uns umas os as o a
sobre entre apos depois antes mais menos como ainda hoje ontem amanha diz disse aponta veja entenda
saiba revela brasil brasileira brasileiro governo pais novo nova contra este esta pelo pela pelos pelas
sob durante segundo apos video ao vivo""".split())
# Nomes que aparecem em muitos fatos diferentes. Sozinhos não bastam para fundir manchetes.
UBIQUOS = set("""lula bolsonaro moraes alexandre stf governo ministro presidente federal brasil
rio paulo flavio congresso senado camara trump putin pt stj tse pf receita banco central
nacional publico estado cidade hoje ontem agora novo nova pais mundo pessoa pessoas caso
diz disse afirma segundo anos ano dia dias mil reais dolar""".split())
RESULTADO = {"vence", "vencem", "vencer", "vitoria", "empata", "empatam", "empate", "perde", "perdem", "derrota", "goleia", "goleado", "classifica", "elimina"}


def tokens(s):
    return {w for w in re.findall(r"[a-z0-9]{3,}", norm(s)) if w not in STOP}


def _numeros(texto):
    return set(re.findall(r"\d+", texto or ""))


def contem_termo(texto, termo):
    t = norm(texto)
    k = norm(termo).strip()
    if not k:
        return False
    if " " in k:
        return k in t
    return re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", t) is not None


def conflito_numeros(texto_a, texto_b):
    """2 a 0 não é 1 a 0. O zero do placar não cola 7 a 0 com 9 a 0.
    1958 e 94 no mesmo fato não brigam, porque o 94 está nos dois.
    Um 3 dentro do 7 a 0 (hat-trick) também não separa o jogo."""
    ga = {n for n in _numeros(texto_a) if int(n) >= 1}
    gb = {n for n in _numeros(texto_b) if int(n) >= 1}
    if not ga or not gb:
        return False
    return bool(ga - gb) and bool(gb - ga)


def mesmo_fato(ta, tb, texto_a="", texto_b=""):
    """Verdadeiro quando dois títulos parecem o mesmo fato.

    A cobertura é medida no título mais curto, para a manchete breve entrar no mesmo
    grupo da manchete longa. Nomes que estão em tudo (Lula, Moraes, Bolsonaro) não
    fundem sozinhos dois fatos. Número ou placar diferente separa.
    """
    if not ta or not tb:
        return False
    if texto_a and texto_b and conflito_numeros(texto_a, texto_b):
        return False
    ra, rb = ta & RESULTADO, tb & RESULTADO
    if ra and rb and ra.isdisjoint(rb):
        return False
    inter = ta & tb
    if len(inter) < 2:
        return False
    especificos = {w for w in inter if w not in UBIQUOS and len(w) >= 4}
    cobertura = len(inter) / max(1, min(len(ta), len(tb)))
    if len(especificos) >= 2 and cobertura >= 0.5:
        return True
    if len(especificos) >= 3 and cobertura >= 0.4:
        return True
    if cobertura >= 0.72 and len(inter) >= 4:
        return True
    return False


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def sem_travessao(s):
    return (s or "").replace("—", ", ").replace("–", ", ").replace("−", "-")


_LIXO_MIDIA = re.compile(
    r"(?is)("
    r"inscreva-se(?:\s+no\s+canal)?[^.!?]*[.!?]?|"
    r"acompanhe mais not[ií]cias[^.!?]*[.!?]?|"
    r"assista\s+(?:às|as|ao)\s+(?:[ií]ntegras|[ao]\s*vivo)[^.!?]*[.!?]?|"
    r"deixe seu like[^.!?]*[.!?]?|"
    r"link na descri[cç][aã]o[^.!?]*[.!?]?|"
    r"nosso whatsapp[^.!?]*[.!?]?|"
    r"(?:https?://)?(?:(?:www\.)?youtube\.com|youtu\.be)[^\s]*|"
    r"(?:https?://)?(?:descubra\s*\.\s*recordplus|jornaldarecord|r7\s*\.\s*com/)[^\s]*|"
    r"[\u2192\u27a1]\s*"
    r")"
)


def limpa_lixo_midia(s):
    """Tira CTA de YouTube/Shorts, hashtag de canal e flecha de chamada."""
    t = s or ""
    t = re.sub(r"#\w+", " ", t)
    t = _LIXO_MIDIA.sub(" ", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def limpa_texto_fonte(s):
    """Tira rodapé, bloco relacionado e lixo de YouTube que o RSS cola no lead."""
    t = sem_travessao(s or "")
    t = re.sub(r"(?is)\s*leia mais(?:\s*\([^)]{0,80}\))?", " ", t)
    t = re.sub(r"(?is)\s*not[ií]cias relacionadas:.*$", " ", t)
    t = limpa_lixo_midia(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def texto_apoio(titulo, descricao=""):
    """Título e descrição/lead da fonte, já limpos, para IA e QA usarem o mesmo material."""
    return " ".join(x for x in (limpa_texto_fonte(titulo), limpa_texto_fonte(descricao)) if x)


def frase_lixo(frase):
    n = norm(frase)
    if re.search(r"leia mais", n):
        return True
    if re.search(r"noticias relacionadas", n):
        return True
    if re.search(
        r"inscreva-se|acompanhe mais noticias|deixe seu like|link na descricao|"
        r"nosso whatsapp|recordplus|jornaldarecord|assista as integras|assista as integras",
        n,
    ):
        return True
    # frase que, depois da limpeza, não sobra fato — só URL, hashtag ou flecha
    if not re.search(r"[a-z]{4,}", norm(limpa_lixo_midia(frase))):
        return True
    return False


SINAIS_FAUNA = (
    "ararinha", "arara-azul", "arara azul", "arara de lear",
    "zoologico", "especie ameacada", "especie em extincao",
    "fauna", "ibama", "biodiversidade", "filhote de arara",
    "nascimento de filhote",
)


LINHA = {"Gazeta do Povo", "Revista Oeste", "Jovem Pan", "O Antagonista", "Diário do Poder", "Pleno.News"}


def limpa_manchete(titulo):
    t = sem_travessao(titulo or "")
    t = re.sub(r"^(urgente|ao vivo|veja|entenda|saiba|exclusivo)\s*[:\-]\s*", "", t, flags=re.I)
    t = limpa_lixo_midia(t)
    t = re.sub(r"\s+", " ", t).strip().rstrip(".")
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def eh_entretenimento(titulo, url="", resumo=""):
    """Lifestyle e celebridade: vale título, endereço da matéria e resumo.

    Um fato político publicado numa editoria de moda continua notícia.
    """
    texto = " ".join(x for x in (titulo, resumo) if x)
    endereco = norm(url or "")
    if any(p in endereco for p in CAMINHOS_ENTRETENIMENTO):
        return not any(contem_termo(texto, k) for k in SINAIS_POLITICA)
    if not any(contem_termo(texto, k) for k in ENTRETENIMENTO):
        return False
    return not any(contem_termo(texto, k) for k in SINAIS_POLITICA)


SINAIS_JUDICIARIO = (
    "stf", "supremo tribunal", "tse", "tcu", "stj", "pgr",
    "procuradoria-geral", "procuradoria geral", "tribunal de contas da uniao",
)
SINAIS_ELEITORAL = (
    "eleicao", "eleicoes", "segundo turno", "primeiro turno",
)
SINAIS_OUTRO_UF = (
    "parana", "curitiba", "londrina", "cascavel", "maringa",
    "rio grande do sul", "porto alegre", "caxias do sul",
    "minas gerais", "belo horizonte", "uberlandia",
    "bahia", "salvador", "pernambuco", "recife",
    "ceara", "fortaleza", "amazonas", "manaus",
    "paraiba", "goias", "goiania", "santa catarina", "florianopolis",
    "mato grosso", "cuiaba", "alagoas", "maceio", "sergipe", "aracaju",
    "no pr", "no rs", "no sc", "no ba", "no pe", "no ce", "no am",
    "no mg", "em curitiba", "em porto alegre", "em recife",
)
SINAIS_CLIMA = (
    "temporal", "temporais", "chuva forte", "enchente", "alagamento", "deslizamento",
)


def tem_sinal_rio(texto):
    if any(contem_termo(texto, k) for k in PALAVRAS["rio"]):
        return True
    n = " " + norm(texto) + " "
    # Rios (Tietê, Negro, Madeira…) e o Rio Grande do Sul não são o estado do Rio.
    if re.search(r"rio (?:grande|tiete|negro|madeira|pinheiros|paraiba|nilo|eufrates|amazon|sao francisco)", n):
        return False
    return re.search(r"(?<![a-z])(?:o|no|do|ao) rio(?![a-z])", n) is not None


def tem_outro_estado(texto):
    return any(contem_termo(texto, k) for k in SINAIS_OUTRO_UF)


def tem_judiciario_ou_eleicao(texto):
    return any(contem_termo(texto, k) for k in SINAIS_JUDICIARIO + SINAIS_ELEITORAL)


def segmento_por_titulo(titulo, dica=""):
    t = norm(titulo)
    # Judiciário e eleição nacionais vêm antes do "Rio de Janeiro" no título.
    if tem_judiciario_ou_eleicao(titulo):
        return "politica"
    for seg in ("educacao", "esportes", "ciencia", "seguranca", "economia", "internacional"):
        if any(contem_termo(t, k) for k in PALAVRAS[seg]):
            return seg
    if tem_sinal_rio(titulo) and not tem_outro_estado(titulo):
        return "rio"
    if dica in PALAVRAS and any(contem_termo(t, k) for k in PALAVRAS[dica]):
        return dica
    # Política só com sinal claro. O residual jogava moda, novela e lista em Política.
    if any(contem_termo(t, k) for k in SINAIS_POLITICA):
        return "politica"
    return None


def ajustar_segmento(titulo, segmento, dica="", url="", resumo=""):
    """Corrige seção óbvia (STF em Economia, temporal do PR em Rio) sem inventar bloco."""
    if eh_entretenimento(titulo, url, resumo):
        return None
    texto = " ".join(x for x in (titulo, resumo) if x)
    if any(contem_termo(texto, k) for k in SINAIS_FAUNA):
        return "ciencia"
    # Escola + temporal no Rio é pauta local, não "educação".
    if any(contem_termo(texto, k) for k in SINAIS_CLIMA) and tem_sinal_rio(texto):
        return "rio"
    # STF, TSE, TCU, PGR, segundo turno: política nacional, mesmo se a cena é uma escola.
    if tem_judiciario_ou_eleicao(texto):
        return "politica"
    # Protocolo/racismo nas escolas, Enem, universidade: educação ganha da Câmara como palco.
    if any(contem_termo(texto, k) for k in PALAVRAS["educacao"]):
        return "educacao"
    auto = segmento_por_titulo(titulo, dica if dica in PALAVRAS else "")
    validos = set(PALAVRAS) | {"politica"}
    if segmento not in validos:
        segmento = auto
    t = norm(titulo)
    if any(contem_termo(t, k) for k in ("flamengo", "vasco", "fluminense", "botafogo", "brasileirao", "libertadores", "uefa", "liga das nacoes")):
        if not any(contem_termo(t, k) for k in ("alerj", "prefeitura", "governo do", "stf", "camara")):
            return "esportes"
    # Rio só com pauta fluminense, nunca outro estado e nunca Judiciário nacional.
    if segmento == "rio" or auto == "rio":
        if tem_judiciario_ou_eleicao(texto):
            return "politica"
        if tem_outro_estado(texto) and not tem_sinal_rio(texto):
            return auto if auto and auto != "rio" else (segmento if segmento and segmento != "rio" else None)
        if tem_sinal_rio(texto):
            return "rio"
        if segmento == "rio":
            return auto if auto and auto != "rio" else None
    if segmento == "economia":
        if tem_judiciario_ou_eleicao(texto):
            return "politica"
        if any(contem_termo(texto, k) for k in PALAVRAS["educacao"]):
            return "educacao"
    if segmento == "politica":
        if any(contem_termo(t, k) for k in ("abono", "salarial", "bolsa familia", "selic")):
            return "economia"
        if any(contem_termo(t, k) for k in ("enem", "ideb", "prouni")):
            return "educacao"
        return segmento
    return segmento if segmento in validos else auto


# Palavras de calendário, verbos de redação e instituições genéricas não fundem fatos.
GENERICO_ASSUNTO = UBIQUOS | {
    "segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo", "feira",
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto",
    "setembro", "outubro", "novembro", "dezembro",
    "nesta", "desta", "neste", "deste", "nessa", "dessa", "hoje", "ontem", "amanha",
    "noite", "manha", "tarde", "proximo", "proxima", "passada", "passado",
    "deixa", "deixou", "foram", "sera", "pode", "depois", "antes", "durante",
    "encerrou", "comeca", "comecou", "afirmou", "disse", "informa", "segundo",
    "decisao", "acordo", "votos", "aprova", "aprovou", "mantem", "analisado",
    "plenario", "equipe", "parte", "apresentacao", "apresentou",
    "tribunal", "supremo", "federal", "nacional", "ministerio", "governo",
    "fiscal", "economica", "economico", "contas", "medida", "medidas",
}


def tokens_assunto(texto):
    return {w for w in tokens(texto) if w not in GENERICO_ASSUNTO and len(w) >= 5}


def _titulo_de(texto):
    """Primeira frase, usada para não fundir fatos só porque o resumo cita o outro."""
    t = (texto or "").strip()
    for sep in (". ", "! ", "? "):
        if sep in t:
            return t.split(sep, 1)[0]
    return t


def mesmo_assunto(texto_a, texto_b):
    """Mesmo episódio com ângulos diferentes (anúncio de tarifas vs. reação do governo)."""
    if not texto_a or not texto_b:
        return False
    inter_tit = tokens_assunto(_titulo_de(texto_a)) & tokens_assunto(_titulo_de(texto_b))
    longos_tit = {w for w in inter_tit if len(w) >= 6}
    if not longos_tit:
        return False
    inter = tokens_assunto(texto_a) & tokens_assunto(texto_b)
    longos = {w for w in inter if len(w) >= 6}
    # Três nomes longos no texto e ao menos um já no título (tarifas, aeronaves).
    return len(longos) >= 3


# Trends cujo nome, sozinho no X Brasil, costuma ser reality, atração ou homônimo
# genérico. A matéria ligada NÃO desambigua: "Fazenda" no título de uma notícia
# sobre o ministério não prova que o trend seja o ministério.
TRENDS_REALITY = (
    "a fazenda", "bbb", "big brother", "big brother brasil", "masterchef",
    "the voice", "no limite", "power couple", "ilha record", "casa dos artistas",
    "bake off", "a clinica", "aclínica", "aclinica",
)
# Nome curto/homônimo: só entra se o PRÓPRIO nome (ou metadado do X) trouxer
# a desambiguação editorial. Sem isso, descarta.
TRENDS_HOMONIMAS = {
    "fazenda": ("ministerio da fazenda", "ministro da fazenda", "fazenda publica",
                "equipe economica", "equipe econômica"),
    "master": ("banco master", "caso master", "financeira master"),
    "record": ("rede record", "record news"),  # sozinha é a emissora/reality
    "prova": ("prova de vida", "prova pericial"),
    "ilha": ("ilha de",),
    "casa": ("casa civil", "casa branca", "camara"),
    "cidade": ("cidade de deus",),
}
SINAIS_REALITY_X = (
    "reality", "peao", "peoes", "roca", "roça", "record tv", "adora a fazenda",
    "rizzo", "participantes da fazenda", "confinamento",
)
SINAIS_TREND_EDITORIAL = (
    "lula", "bolsonaro", "stf", "moraes", "congresso", "senado", "camara",
    "governo", "ministro", "ministerio", "eleicao", "tse", "tcu", "pgr",
    "ipca", "inflacao", "imposto", "tarifa", "orcamento", "fiscal", "dolar",
    "selic", "banco central", "fazenda publica", "pf", "planalto",
)


def _texto_do_trend(nome, trend=None):
    """Só o que veio do X (nome e metadados). Nunca o título da matéria ligada."""
    partes = [nome or ""]
    if isinstance(trend, dict):
        for k in ("nome", "query", "descricao", "contexto_x", "tema_x", "annotation"):
            v = trend.get(k)
            if v and k != "sobre":
                partes.append(str(v))
    return " ".join(partes)


def trend_nome_ambiguo(nome, trend=None):
    """True se o termo em alta é reality/homônimo sem desambiguação no próprio X."""
    bruto = (nome or "").strip()
    if not bruto:
        return True
    n = norm(bruto)
    compacto = n.replace("#", " ").strip()
    # "Ministério da Fazenda" contém a substring "a fazenda"; isso não é o reality.
    if any(d in compacto for d in (
        "ministerio da fazenda", "ministro da fazenda", "fazenda publica",
        "banco master", "caso master", "casa civil", "casa branca",
    )):
        return False
    if compacto in TRENDS_REALITY or compacto == "fazenda":
        return True
    if any(r != "a fazenda" and (contem_termo(compacto, r) or compacto == r) for r in TRENDS_REALITY):
        return True
    if re.search(r"(?<![a-z0-9])a fazenda(?![a-z0-9])", compacto):
        return True
    if any(contem_termo(_texto_do_trend(bruto, trend), s) for s in SINAIS_REALITY_X):
        return True
    toks = re.findall(r"[a-z0-9]{3,}", compacto)
    chave = compacto if compacto in TRENDS_HOMONIMAS else (toks[0] if len(toks) == 1 and toks[0] in TRENDS_HOMONIMAS else "")
    if not chave:
        return False
    exigidos = TRENDS_HOMONIMAS[chave]
    if not exigidos:
        return True
    contexto = norm(_texto_do_trend(bruto, trend))
    return not any(e in contexto for e in exigidos)


def trend_casa_no_item(nome, item, trend=None):
    """O nome do trend precisa estar no título (nome curto) ou, se composto, amarrado à matéria.

    Homônimo de reality/entretenimento sem desambiguação no próprio X não casa
    com notícia política ou econômica só porque a palavra aparece no título.
    """
    if trend_nome_ambiguo(nome, trend):
        return False
    nome_n = norm(nome or "").replace("#", " ").strip()
    if not nome_n:
        return False
    titulo = item.get("title") or ""
    texto = titulo + " " + (item.get("summary") or "")
    toks = re.findall(r"[a-z0-9]{3,}", nome_n)
    if not toks:
        return False
    if len(toks) == 1:
        # "André" no meio do resumo do Palmeiras não basta; precisa estar no título.
        return contem_termo(titulo, toks[0])
    if contem_termo(titulo, nome_n) or contem_termo(texto, nome_n):
        return True
    if all(contem_termo(texto, w) for w in toks):
        return True
    longo = max(toks, key=len)
    return len(longo) >= 6 and contem_termo(titulo, longo)


def _uso_robotico(uso, titulo):
    u = norm(uso or "")
    t = norm(titulo or "")
    if not u:
        return True
    if u.startswith("explicar a noticia") or u.startswith("explicar a matéria") or u.startswith("explicar a materia"):
        return True
    if t and t in u and len(u) <= len(t) + 40:
        return True
    return False


def _uso_padrao(item):
    """Orientação editorial curta, sem repetir o título da matéria."""
    texto = norm((item.get("title") or "") + " " + (item.get("summary") or "") + " " + (item.get("segment") or item.get("bloco") or ""))
    if any(contem_termo(texto, k) for k in ("ipca", "inflacao", "dolar", "imposto", "tarifa", "orcamento", "teto", "selic", "fazenda", "gasto publico", "contas publicas")):
        return "Abrir pelo impacto no bolso e nas contas públicas, com os números da matéria, e cobrar a responsabilidade do governo."
    if any(contem_termo(texto, k) for k in ("stf", "tse", "tcu", "moraes", "judiciario", "supremo")):
        return "Cobrar o que o fato revela sobre o alcance do Judiciário e das instituições, com o que está na matéria."
    if any(contem_termo(texto, k) for k in ("lula", "governo", "planalto", "ministerio", "pt", "denuncia", "pgr")):
        return "Enquadrar o fato como denúncia dos erros e absurdos do governo e das instituições, sem suavizar, e sem afirmar crime sem condenação."
    if any(contem_termo(texto, k) for k in ("corinthians", "flamengo", "palmeiras", "brasileirao", "libertadores")):
        return "Comentar o resultado esportivo a partir da matéria ligada, sem puxar outro assunto."
    return "Usar o fato da matéria ligada para abrir o comentário, com o enquadramento do programa, sem repetir o título."


def uso_coerente(uso, item):
    """Garante uso coerente com a matéria e natural (nunca 'Explicar a notícia: <título>')."""
    titulo = limpa_manchete(item.get("title") or "")
    texto = (item.get("title") or "") + " " + (item.get("summary") or "")
    uso = (uso or "").strip()
    permitidos = {
        "explicar", "rebater", "amplificar", "comentar", "usar", "abordar",
        "fato", "noticia", "materia", "impacto", "desdobramento", "politico",
        "ligar", "mencionar", "citar", "abrir", "pautar", "cobrar", "bolso",
        "governo", "contas", "publicas", "denuncia",
    }
    if uso and not _uso_robotico(uso, titulo):
        extra = {w for w in tokens(uso) if w not in UBIQUOS and w not in permitidos and len(w) >= 5}
        if not extra or extra <= tokens(texto):
            return sem_travessao(uso)
    return _uso_padrao(item)


def cobertura(n):
    if n >= 9:
        return 10
    if n >= 6:
        return round(8 + (n - 6) * (10 - 8) / 3, 1)
    if n >= 3:
        return round(5 + (n - 3) * (8 - 5) / 3, 1)
    if n >= 1:
        return round(2 + (n - 1) * (5 - 2) / 2, 1)
    return 0


def destaque(outlets, tiers):
    """Sem posição de home no RSS: a nota usa tier e quantos veículos deram o fato."""
    n = len(outlets)
    t1 = sum(1 for o in outlets if tiers.get(o.get("name"), 3) == 1)
    t2 = sum(1 for o in outlets if tiers.get(o.get("name"), 3) == 2)
    if t1 >= 4 or (t1 >= 2 and n >= 8):
        return 8
    if t1 >= 2 or (t1 >= 1 and n >= 5):
        return 6
    if t1 >= 1 or t2 >= 3 or n >= 4:
        return 5
    if n >= 2:
        return 4
    return 3


def persistencia(desdobramento, viu_edicao_anterior, atravessa_dia):
    if atravessa_dia and desdobramento:
        return 10
    if viu_edicao_anterior and desdobramento:
        return 7
    if viu_edicao_anterior:
        return 7
    return 4


def nota_final(scores, pesos):
    return round(sum(scores[k] * pesos[k] for k in pesos) / sum(pesos.values()), 1)


def assimetria(outlets, disponiveis):
    """Selo só quando dá para comparar os dois campos (3 ou mais portais de cada na coleta)
    e o fato está de um lado e ausente do outro.
    disponiveis: {nome: camp} dos portais que de fato entregaram manchete nesta rodada.
    """
    dir_ok = [n for n, c in disponiveis.items() if c in DIREITA]
    esq_ok = [n for n, c in disponiveis.items() if c in ESQUERDA]
    if len(dir_ok) < 3 or len(esq_ok) < 3:
        return False, ""
    d = [o for o in outlets if o.get("camp") in DIREITA]
    e = [o for o in outlets if o.get("camp") in ESQUERDA]
    if len(d) >= 2 and len(e) == 0:
        nomes = ", ".join(o["name"] for o in d[:6])
        return True, f"Teve destaque na direita ({nomes}) e não apareceu nos portais de esquerda e centro-esquerda da coleta."
    if len(e) >= 2 and len(d) == 0:
        nomes = ", ".join(o["name"] for o in e[:6])
        return True, f"Teve destaque na esquerda e no centro-esquerda ({nomes}) e não apareceu nos portais de direita da coleta."
    return False, ""


def id_edicao(agora):
    faixa = "06h" if agora.hour < 9 else "12h" if agora.hour < 16 else "19h"
    return agora.strftime("%Y-%m-%d") + "-" + faixa


def peso_voce(valor, segmento):
    try:
        v = float(valor)
    except (TypeError, ValueError):
        v = 10 if segmento in ("politica", "seguranca", "economia", "rio") else 6
    for alvo in (3, 6, 10):
        if abs(v - alvo) <= 1.6:
            return alvo
    return 6


def _self_test():
    assert cobertura(1) == 2
    assert cobertura(3) == 5
    assert cobertura(6) == 8
    assert cobertura(9) == 10
    assert eh_entretenimento("Robert Pattinson é anunciado como novo James Bond")
    assert not eh_entretenimento("Pattinson critica o governo Lula em Cannes")
    assert segmento_por_titulo("Flamengo vence o Vasco no Maracanã") == "esportes"
    assert segmento_por_titulo("Alerj vota o orçamento do Rio") == "rio"
    assert ajustar_segmento("Robert Pattinson é o novo James Bond", "politica") is None
    flag, nota = assimetria(
        [{"name": "Gazeta do Povo", "camp": "direita"}, {"name": "Revista Oeste", "camp": "direita"},
         {"name": "Poder360", "camp": "centro"}],
        {"Gazeta do Povo": "direita", "Revista Oeste": "direita", "Jovem Pan": "direita",
         "Folha de S.Paulo": "centro-esquerda", "UOL": "centro-esquerda", "CartaCapital": "esquerda"},
    )
    assert flag and "Gazeta" in nota
    flag2, _ = assimetria(
        [{"name": "Gazeta do Povo", "camp": "direita"}, {"name": "Folha de S.Paulo", "camp": "centro-esquerda"}],
        {"Gazeta do Povo": "direita", "Revista Oeste": "direita", "Jovem Pan": "direita",
         "Folha de S.Paulo": "centro-esquerda", "UOL": "centro-esquerda", "CartaCapital": "esquerda"},
    )
    assert not flag2
    pesos = {"cobertura": 10, "destaque": 8, "persistencia": 6, "peso_voce": 7}
    scores = {"cobertura": 10, "destaque": 6, "persistencia": 4, "peso_voce": 10}
    assert nota_final(scores, pesos) == round((100 + 48 + 24 + 70) / 31, 1)
    a = tokens("Moraes nega visita de Marinho e Tereza Cristina a Bolsonaro")
    b = tokens("Alexandre de Moraes barra visita de Rogerio Marinho e Tereza Cristina a Bolsonaro")
    c = tokens("Moraes abre inquerito contra jornalista da Folha")
    assert mesmo_fato(a, b, "Moraes nega visita de Marinho e Tereza Cristina a Bolsonaro", "Alexandre de Moraes barra visita de Rogerio Marinho e Tereza Cristina a Bolsonaro")
    assert not mesmo_fato(a, c, "Moraes nega visita de Marinho", "Moraes abre inquerito contra jornalista")
    assert not mesmo_fato(tokens("Flamengo vence o Vasco por 2 a 0"), tokens("Flamengo vence o Vasco por 1 a 0"), "Flamengo vence o Vasco por 2 a 0", "Flamengo vence o Vasco por 1 a 0")
    assert conflito_numeros("Brasil goleia Singapura por 7 a 0", "Brasil Sub-20 faz 9 a 0 no Peru")
    assert not conflito_numeros("Brasil goleia Singapura por 7 a 0", "Vini marca 3 no 7 a 0 sobre Singapura")
    assert segmento_por_titulo("Alemanha faz 5 na Holanda e vai às quartas de final da Liga das Nações da Uefa") == "esportes"
    assert segmento_por_titulo("Defesa de Carla Zambelli pede retirada de nome da lista da Interpol") is None
    assert segmento_por_titulo("STF pode julgar Lei da Dosimetria ainda este ano") == "politica"
    assert segmento_por_titulo("Verão 2027 troca o excesso de decoração por unhas curtas e acabamento impecável") is None
    assert eh_entretenimento("Verão 2027 troca o excesso de decoração por unhas curtas e acabamento impecável")
    assert eh_entretenimento(
        "Ismália arma contra o próprio marido, Cesário",
        "https://oglobo.globo.com/play/novelas/noticia/2026/11/18/la-na-minha-terra.ghtml",
    )
    assert eh_entretenimento("x", "https://oglobo.globo.com/ela/noticia/2026/11/18/verao-2027.ghtml")
    assert not eh_entretenimento(
        "Lula visita exposição de moda no Itamaraty",
        "https://oglobo.globo.com/ela/noticia/2026/11/18/lula.ghtml",
    )
    assert segmento_por_titulo("Mais de 276 mil trabalhadores ainda não sacaram o abono salarial") == "economia"
    assert ajustar_segmento("Mais de 276 mil trabalhadores ainda não sacaram o abono salarial", "politica") == "economia"
    assert ajustar_segmento("Senado vota a PEC do STF", "politica") == "politica"
    assert ajustar_segmento("PF entrega relatório sobre ministros do STF no caso Master após a prisão", "politica") == "politica"
    assert ajustar_segmento(
        "Nasce filhote de ararinha-azul no Zoológico de São Paulo",
        "educacao",
        resumo="O filhote de ararinha-azul nasceu no Zoológico de São Paulo, espécie que chegou a ser extinta na natureza.",
    ) == "ciencia"
    assert "ararinha" in PALAVRAS["ciencia"]
    assert limpa_texto_fonte("O Corinthians empatou. Leia mais (11/25/2026 - 22h09)") == "O Corinthians empatou."
    assert frase_lixo("Leia mais (11/25/2026 - 22h09)")
    assert not frase_lixo("O Corinthians empatou com o Atlético-MG por 1 a 1.")
    assert segmento_por_titulo("Juiz anula limites de visto impostos por Trump a estudantes e jornalistas") == "internacional"
    assert eh_entretenimento("Grammy 2027 é no meio do carnaval, diz Anitta")
    assert not eh_entretenimento("Irmãos de banda de pagode são presos por suspeita de estupro")

    # 2. YouTube/Shorts e seção Rio só com pauta fluminense
    assert "JR24H" not in limpa_manchete("Temporais no PR atingem 50 cidades e deixam seis mortos. #JR24H #Shorts")
    assert "Shorts" not in limpa_manchete("Temporais no PR atingem 50 cidades e deixam seis mortos. #JR24H #Shorts")
    assert limpa_manchete("Temporais no PR atingem 50 cidades e deixam seis mortos. #JR24H #Shorts") == (
        "Temporais no PR atingem 50 cidades e deixam seis mortos"
    )
    cta = (
        "➡️ Acompanhe mais notícias em nosso site oficial: https://noticias.com/jr-na-tv "
        "Inscreva-se no canal Jornal da Record."
    )
    assert frase_lixo(cta)
    assert "inscreva" not in norm(limpa_texto_fonte(cta))
    assert ajustar_segmento("Temporais no PR atingem 50 cidades e deixam seis mortos", "rio") != "rio"
    assert ajustar_segmento("Bombeiros suspendem buscas por caminhoneiro desaparecido no Rio Tietê", "rio") != "rio"
    assert ajustar_segmento(
        "Chuva forte atinge o Rio e a Região Metropolitana; Uerj e UFRJ suspendem aulas",
        "economia",
        resumo="A chuva atinge a Zona Norte do Rio. A Uerj e a UFRJ suspendem as aulas.",
    ) == "rio"

    # 3. Mesmo assunto (tarifas) x fatos distintos
    t1 = "EUA propõem novas tarifas sobre produtos brasileiros, incluindo aeronaves e açúcar. O Planalto reagiu à alíquota de 10% sobre aeronaves da Embraer e o açúcar."
    t2 = "Governo brasileiro manifesta indignação e não cederá a ameaças de tarifas dos EUA. A medida atinge aeronaves e açúcar produzidos no Brasil."
    t3 = "Governo Lula reage a ameaça de tarifas dos EUA e cita pressão sobre o Pix. As tarifas incluem aeronaves e açúcar."
    t4 = "Lula sondou Luiz Carlos Trabuco para o Ministério da Fazenda. Trabuco avalia o convite do presidente."
    t5 = "PGR denuncia Gustavo Gayer ao STF por desvio de recursos públicos."
    assert mesmo_assunto(t1, t2) and mesmo_assunto(t2, t3) and mesmo_assunto(t1, t3)
    assert not mesmo_assunto(t1, t4) and not mesmo_assunto(t4, t5)
    assert not mesmo_assunto(
        "Corinthians empata com Atlético-MG em Belo Horizonte nesta quarta-feira.",
        "Homem é condenado a 28 anos por matar ex-noiva a facadas em Belo Horizonte. A vítima se chamava Vitória.",
    )
    assert not mesmo_assunto(
        "TCU afasta multas a Dilma Rousseff por irregularidades fiscais. A equipe econômica acompanha a decisão nesta quarta-feira.",
        "Equipe econômica avalia teto de 2,5% para despesas obrigatórias em novo pacote de ajuste fiscal nesta quarta-feira.",
    )

    # 4. Taxonomia: STF/eleição não vai a economia nem a rio; escolas não vão a economia
    assert ajustar_segmento(
        "STF marca para 4 de dezembro o início do julgamento sobre segundo turno no Rio de Janeiro",
        "economia",
    ) == "politica"
    assert ajustar_segmento(
        "STF marca para 4 de dezembro julgamento sobre segundo turno no Rio de Janeiro",
        "rio",
    ) == "politica"
    assert ajustar_segmento(
        "Câmara aprova protocolo nacional para combater racismo nas escolas",
        "economia",
        resumo="A Câmara dos Deputados aprovou o projeto que institui um protocolo nacional de acolhimento às vítimas de racismo nas escolas.",
    ) == "educacao"
    assert ajustar_segmento("Senado vota a PEC do STF", "economia") == "politica"
    assert ajustar_segmento(
        "Moraes autoriza Débora do Batom a comparecer à apresentação do filho",
        "politica",
        resumo="O ministro Alexandre de Moraes, do STF, autorizou a saída para a cantata em uma escola de Paulínia.",
    ) == "politica"

    # 5. Trends: nome genérico só no título; uso deslocado é reescrito
    pal = {"title": "Palmeiras viaja para final da Libertadores com festa da torcida", "summary": "O Palmeiras embarcou com André e a delegação."}
    cor = {"title": "Corinthians empata com Atlético-MG e sai da zona de rebaixamento no Brasileirão", "summary": "O Corinthians empatou com o Atlético-MG em Belo Horizonte."}
    faz = {"title": "Dólar cai abaixo de R$ 5,80 após Lula sondar Luiz Trabuco para Fazenda", "summary": "O presidente sondou Luiz Carlos Trabuco para o Ministério da Fazenda."}
    assert not trend_casa_no_item("André", pal)
    assert trend_casa_no_item("Corinthians", cor)
    assert trend_casa_no_item("Atlético Mineiro", cor)
    # "Fazenda" no X Brasil é o reality, não o ministério, mesmo que a matéria cite a pasta.
    assert trend_nome_ambiguo("Fazenda")
    assert trend_nome_ambiguo("A Fazenda")
    assert trend_nome_ambiguo("BBB")
    assert not trend_casa_no_item("Fazenda", faz)
    assert not trend_casa_no_item("A Fazenda", faz)
    assert trend_casa_no_item("Ministério da Fazenda", faz)
    assert not trend_nome_ambiguo("Ministério da Fazenda")
    uso = uso_coerente("Explicar impacto do julgamento para eleições no Rio", cor)
    assert "eleicoes" not in norm(uso) and "julgamento" not in norm(uso)
    assert "explicar a noticia" not in norm(uso)
    assert "Dólar cai abaixo" not in uso_coerente("", faz)
    uso_faz = uso_coerente("Explicar a notícia: Dólar cai abaixo de R$ 5,80 após Lula sondar Luiz Trabuco para Fazenda", faz)
    assert not uso_faz.lower().startswith("explicar a notícia:")
    assert "bolso" in norm(uso_faz) or "contas publicas" in norm(uso_faz) or "governo" in norm(uso_faz)

    print("noticias_notas: ok")


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print("uso: python3 tools/noticias_notas.py --self-test", file=sys.stderr)
        sys.exit(2)


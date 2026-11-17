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
)
SINAIS_POLITICA = (
    "lula", "bolsonaro", "stf", "moraes", "congresso", "senado", "camara",
    "governo", "ministro", "eleicao", "eleicoes", "tse",     "pf", "policia federal",
    "pl", "pt", "stj", "inquerito", "prisao", "preso", "policia", "operacao",
)
PALAVRAS = {
    "educacao": ("enem", "escola", "educacao", "aluno", "professor", "universidade",
                 "vestibular", "inep", "enade", "fies", "prouni", "mec", "faetec",
                 "uerj", "uenf", "cederj", "cecierj", "seeduc", "ideb", "saeb", "pisa"),
    "esportes": ("flamengo", "vasco", "fluminense", "botafogo", "palmeiras", "corinthians",
                 "futebol", "copa do brasil", "libertadores", "selecao brasileira",
                 "brasileirao", "cbf", "olimp", "volei", "formula 1", "tenis",
                 "uefa", "liga das nacoes", "copa do mundo", "champions", "ancelotti",
                 "eliminatorias", "amistoso", "atp", "wta"),
    "ciencia": ("inteligencia artificial", "tecnologia", "ciencia", "cientista", "pesquisa",
                "startup", "nvidia", "openai", "chatgpt", "nasa", "satelite", "foguete",
                "cnpq", "capes", "finep", "faperj", "vacina", "anvisa"),
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
    """2 a 0 não é 1 a 0. 1958 e 94 no mesmo fato não brigam, porque o 94 está nos dois."""
    ga = {n for n in _numeros(texto_a) if int(n) >= 1}
    gb = {n for n in _numeros(texto_b) if int(n) >= 1}
    if not ga or not gb:
        return False
    return ga.isdisjoint(gb)


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


LINHA = {"Gazeta do Povo", "Revista Oeste", "Jovem Pan", "O Antagonista", "Diário do Poder", "Pleno.News"}


def limpa_manchete(titulo):
    t = sem_travessao(titulo or "")
    t = re.sub(r"^(urgente|ao vivo|veja|entenda|saiba|exclusivo)\s*[:\-]\s*", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip().rstrip(".")
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def eh_entretenimento(titulo):
    if not any(contem_termo(titulo, k) for k in ENTRETENIMENTO):
        return False
    return not any(contem_termo(titulo, k) for k in SINAIS_POLITICA)


def segmento_por_titulo(titulo, dica=""):
    t = norm(titulo)
    for seg in ("educacao", "esportes", "ciencia", "rio", "seguranca", "economia", "internacional"):
        if any(contem_termo(t, k) for k in PALAVRAS[seg]):
            return seg
    if dica in PALAVRAS and any(contem_termo(t, k) for k in PALAVRAS[dica]):
        return dica
    return "politica"


def ajustar_segmento(titulo, segmento, dica=""):
    """Corrige seção óbvia (futebol em Política, Bond em Política) sem inventar bloco."""
    if eh_entretenimento(titulo):
        return None
    auto = segmento_por_titulo(titulo, dica if dica in PALAVRAS else "")
    validos = set(PALAVRAS) | {"politica"}
    if segmento not in validos:
        segmento = auto
    t = norm(titulo)
    if any(contem_termo(t, k) for k in ("flamengo", "vasco", "fluminense", "botafogo", "brasileirao", "libertadores", "uefa", "liga das nacoes")):
        if not any(contem_termo(t, k) for k in ("alerj", "prefeitura", "governo do", "stf", "camara")):
            return "esportes"
    # A seção que a IA escolheu vale. Política só cede quando o título tem
    # palavra clara de outra editoria (abono salarial, por exemplo).
    if segmento == "politica" and auto != "politica":
        return auto
    return segmento


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
    assert segmento_por_titulo("Alemanha faz 5 na Holanda e vai às quartas de final da Liga das Nações da Uefa") == "esportes"
    assert segmento_por_titulo("Defesa de Carla Zambelli pede retirada de nome da lista da Interpol") == "politica"
    assert segmento_por_titulo("Mais de 276 mil trabalhadores ainda não sacaram o abono salarial") == "economia"
    assert ajustar_segmento("Mais de 276 mil trabalhadores ainda não sacaram o abono salarial", "politica") == "economia"
    assert ajustar_segmento("Senado vota a PEC do STF", "politica") == "politica"
    assert segmento_por_titulo("Juiz anula limites de visto impostos por Trump a estudantes e jornalistas") == "internacional"
    assert eh_entretenimento("Grammy 2027 é no meio do carnaval, diz Anitta")
    assert not eh_entretenimento("Irmãos de banda de pagode são presos por suspeita de estupro")
    print("noticias_notas: ok")


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print("uso: python3 tools/noticias_notas.py --self-test", file=sys.stderr)
        sys.exit(2)

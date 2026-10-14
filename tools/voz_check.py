"""Confere a voz editorial de um texto (artigo, roteiro, legenda): quantas vezes cita veículos
e se usa ressalvas que enfraquecem a linha. Uso:
  python3 tools/voz_check.py artigos/<id>.json        (confere o campo "texto")
  python3 tools/voz_check.py --texto "..."            (confere um texto solto)
Sai com erro (1) se passar do limite; imprime o que achou."""
import json, os, re, sys, unicodedata
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
if sys.argv[1] == "--texto": txt, limite = sys.argv[2], 1
else: txt, limite = json.load(open(sys.argv[1], encoding="utf-8")).get("texto", ""), 2
def n(s): return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
T = n(txt)
nomes = [o["name"] for o in json.load(open("noticias/config.json", encoding="utf-8"))["outlets"]] + ["Agência Brasil", "Globo", "Folha", "Estadão", "CNN"]
AMBIG = {"O Dia", "O Povo", "O Tempo", "Extra", "Veja", "Exame", "Meio", "Valor", "Terra", "Oeste", "A Tarde", "O Popular", "Opera Mundi"}
def tem(x):
    if x in AMBIG:  # nome que também é palavra comum: só conta com maiúscula e fora do início de frase
        return re.search(r"(?<![.!?]\s)(?<!^)(?<=[a-zà-ú,;:]\s)" + re.escape(x) + r"\b", txt) is not None
    return re.search(r"\b" + re.escape(n(x)) + r"\b", T) is not None
cit = sorted({x for x in nomes if tem(x)})
atrib = len(re.findall(r"\b(segundo (o|a|os|as) |de acordo com|conforme (o|a) |informa(m)? que|registra(m)? que|noticiou|reportagem d)", T))
encad = re.findall(r"\b(segundo|de acordo com|conforme)\s+[^.,;]{2,40}\s+e\s+(o|a)\s+[A-Za-z]", T)
RESSALVAS = ["de um lado", "por outro lado", "os dois lados", "ambos os lados", "pode ajudar, mas", "e verdade que o governo", "e justo reconhecer",
             "dureza sem execucao", "vira so discurso", "precisa vir com artigo e custo", "a conta precisa fechar", "sem gestao vira escritorio do crime"]
res = [r for r in RESSALVAS if r in T]
erros = []
if len(cit) > limite: erros.append(f"cita {len(cit)} veículos no texto ({', '.join(cit)}); o limite é {limite}: deixe as fontes no campo de fontes")
if atrib > limite: erros.append(f"{atrib} atribuições do tipo 'segundo/de acordo com'; o limite é {limite}")
if encad: erros.append("encadeia veículos numa mesma atribuição")
if res: erros.append("ressalvas que enfraquecem a linha: " + "; ".join(res))
print("\n".join(erros) or "voz ok")
sys.exit(1 if erros else 0)

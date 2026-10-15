"""Acerta a abertura e o fecho obrigatórios do artigo antes da validação, sem mexer no resto do texto.
- Abertura: "RIO DE JANEIRO, <dia> de <mês> de <ano> — " (usa a data que já estava no artigo; senão, hoje).
- Fecho: a apresentação do autor como último parágrafo.
Uso: python3 tools/artigo_normalizar.py artigos/<id>.json [/tmp/original.json]"""
import json, re, sys
from datetime import datetime, timedelta, timezone
BIO = "Rogerio Pires é jornalista, professor, pesquisador, mestre, doutor H.C. em educação e gestor público com atuação na área de educação tecnológica e políticas de inovação no Estado do Rio de Janeiro."
MESES = "janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro".split()
DATA = r"(\d{1,2}) de ([a-zç]+) de (\d{4})"
path = sys.argv[1]
a = json.load(open(path, encoding="utf-8"))
t = (a.get("texto") or "").replace("\r\n", "\n").strip()
m0 = re.match(r"^[\"'“”*#\s]+(?=rio de janeiro)", t, re.I)  # aspas ou markdown antes da abertura
if m0: t = t[m0.end():]
data = None
for fonte in [t] + ([json.load(open(sys.argv[2], encoding="utf-8")).get("texto", "")] if len(sys.argv) > 2 else []):
    m = re.match(r"\s*rio de janeiro,?\s*" + DATA, fonte or "", re.I)
    if m: data = f"{int(m.group(1))} de {m.group(2).lower()} de {m.group(3)}"; break
if not data:
    h = datetime.now(timezone(timedelta(hours=-3))); data = f"{h.day} de {MESES[h.month-1]} de {h.year}"
abertura = f"RIO DE JANEIRO, {data} — "
m = re.match(r"\s*rio de janeiro,?\s*(" + DATA + r")?\s*[—–\-:,.]?\s*", t, re.I)
corpo = t[m.end():] if m else t
t = abertura + corpo.lstrip()
paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
paras = [p for p in paras if not p.strip().startswith("Rogerio Pires é jornalista")]
t = "\n\n".join(paras + [BIO])
if t != a.get("texto"):
    a["texto"] = t
    json.dump(a, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("abertura/fecho acertados")
else:
    print("abertura/fecho já corretos")

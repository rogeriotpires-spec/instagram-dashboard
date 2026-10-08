"""Junta as regras novas (novos-aprendizados.json) em artigos/aprendizados.json.
Uso: python3 tools/artigo_aprender.py <arquivo-novos> <id-do-artigo>"""
import json, os, sys, re
from datetime import datetime, timezone, timedelta
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
src, aid = sys.argv[1], sys.argv[2]
try: novos = json.load(open(src, encoding="utf-8"))
except Exception: novos = []
path = "artigos/aprendizados.json"
base = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"itens": []}
norm = lambda t: re.sub(r"\W+", " ", t.lower()).strip()
vistos = {norm(i["regra"]) for i in base["itens"]}
hoje = datetime.now(timezone(timedelta(hours=-3))).date().isoformat()
n = 0
for x in novos if isinstance(novos, list) else []:
    r = str(x.get("regra", "")).strip()[:400]
    if not r or norm(r) in vistos or "—" in r or "–" in r: continue
    base["itens"].append({"data": hoje, "regra": r, "comentario": str(x.get("comentario", ""))[:600], "artigo": aid})
    vistos.add(norm(r)); n += 1
base["itens"] = base["itens"][-80:]
json.dump(base, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"aprendizados: +{n} (total {len(base['itens'])})")

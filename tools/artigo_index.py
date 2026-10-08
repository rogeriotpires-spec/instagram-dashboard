"""Reconstrói artigos/index.json a partir dos arquivos artigos/<id>.json."""
import json, glob, os
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
items = []
for f in glob.glob("artigos/*.json"):
    if f.endswith("index.json"): continue
    try: a = json.load(open(f, encoding="utf-8"))
    except Exception: continue
    items.append({k: a.get(k) for k in ("id", "criado", "saved_at", "titulo", "status", "link")} | {"origem": (a.get("origem") or {}).get("rotulo")})
items.sort(key=lambda x: x.get("criado") or "", reverse=True)
json.dump({"items": items}, open("artigos/index.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"index: {len(items)} artigos")

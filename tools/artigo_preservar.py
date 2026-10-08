"""Na reescrita, garante que o título e o subtítulo que o Rogerio deixou no artigo continuem iguais
(a menos que o pedido de reescrita fale em mudar título/subtítulo)."""
import json, sys
path, ped_path = sys.argv[1], sys.argv[2]
try: ped = json.load(open(ped_path, encoding="utf-8"))
except Exception: sys.exit(0)
if ped.get("modo") != "reescrever": sys.exit(0)
fb = (ped.get("feedback") or "").lower()
if "títul" in fb or "titul" in fb: sys.exit(0)
at = ped.get("atual") or {}
a = json.load(open(path, encoding="utf-8"))
mud = False
for k in ("titulo", "subtitulo"):
    if at.get(k) and a.get(k) != at[k]: a[k] = at[k]; mud = True
par = {"titulo": a["titulo"], "subtitulo": a["subtitulo"]}
tt = [x for x in a.get("titulos") or [] if not (x.get("titulo") == par["titulo"] and x.get("subtitulo") == par["subtitulo"])]
a["titulos"] = [par] + tt[:4]; a["escolha"] = 0
json.dump(a, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("título/subtítulo preservados" + (" (restaurados)" if mud else ""))

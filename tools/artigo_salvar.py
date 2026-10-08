"""Grava as edições feitas na página de artigos (dado vindo do botão Salvar).
Uso: PAYLOAD='{"id":...}' python3 tools/artigo_salvar.py
Só altera campos editáveis de um artigo que já existe."""
import json, os, re, sys
from datetime import datetime, timezone, timedelta
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
p = json.loads(os.environ["PAYLOAD"])
aid = str(p.get("id", ""))
if not re.fullmatch(r"[a-z0-9-]{6,90}", aid): sys.exit("id inválido")
path = f"artigos/{aid}.json"
if not os.path.exists(path): sys.exit("artigo não existe")
a = json.load(open(path, encoding="utf-8"))
s = lambda v, n: str(v)[:n] if v is not None else ""
for k, n in (("titulo", 400), ("subtitulo", 800), ("texto", 40000), ("palavras_chave", 1000)):
    if k in p: a[k] = s(p[k], n)
if isinstance(p.get("titulos"), list):
    a["titulos"] = [{"titulo": s(t.get("titulo"), 400), "subtitulo": s(t.get("subtitulo"), 800)} for t in p["titulos"][:10] if isinstance(t, dict)]
for k in ("escolha", "escolha_sub"):
    if isinstance(p.get(k), int): a[k] = p[k]
if p.get("status") in ("rascunho", "finalizado", "publicado"): a["status"] = p["status"]
if "link" in p:
    link = s(p["link"], 500).strip()
    if link and not re.match(r"^https://[^\s\"'<>]+$", link): sys.exit("link inválido")
    a["link"] = link
    if link: a["status"] = "publicado"
a["saved_at"] = s(p.get("saved_at"), 40) or datetime.now(timezone(timedelta(hours=-3))).replace(microsecond=0).isoformat()
json.dump(a, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("salvo", aid, a["status"])

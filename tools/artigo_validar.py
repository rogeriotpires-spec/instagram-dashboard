"""Confere o artigo gerado antes de publicar."""
import json, sys
a = json.load(open(sys.argv[1], encoding="utf-8"))
BIO = "Rogerio Pires é jornalista, professor, pesquisador, mestre, doutor H.C. em educação e gestor público com atuação na área de educação tecnológica e políticas de inovação no Estado do Rio de Janeiro."
erros = []
t = a.get("texto", "")
if not t.startswith("RIO DE JANEIRO, "): erros.append("texto não começa com RIO DE JANEIRO, ")
if not t.rstrip().endswith(BIO): erros.append("texto não termina com a apresentação do autor")
corpo = t.split(" — ", 1)[1] if " — " in t[:60] else t
if "—" in corpo or "–" in corpo: erros.append("travessão no corpo do texto")
tt = a.get("titulos") or []
if len(tt) != 5 or not all(x.get("titulo") and x.get("subtitulo") for x in tt): erros.append("precisa de 5 títulos com subtítulo")
if any(c in json.dumps(tt, ensure_ascii=False) for c in "—–"): erros.append("travessão em título")
if len([k for k in (a.get("palavras_chave") or "").split(",") if k.strip()]) < 5: erros.append("menos de 5 palavras-chave")
if not a.get("titulo") or not a.get("subtitulo"): erros.append("sem título/subtítulo escolhido")
if erros: sys.exit("artigo inválido: " + "; ".join(erros))
print("ok:", len(t.split()), "palavras")

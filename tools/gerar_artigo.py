"""Escreve ou reescreve um artigo da Revista Timeline a partir de pedido-artigo.json."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ia_comum as C
import modelos_ia

ROOT = C.ROOT
BIO = "Rogerio Pires é jornalista, professor, pesquisador, mestre, doutor H.C. em educação e gestor público com atuação na área de educação tecnológica e políticas de inovação no Estado do Rio de Janeiro."
INICIO_PROMPT = "Crie uma imagem horizontal de 1200 x 630 pixels (proporção 1,91:1), em fotojornalismo cinematográfico, alta definição e aparência fotográfica real, para a capa de um artigo da Revista Timeline."
FRASES_OBRIG = [
    "Sem nenhum texto sobreposto: sem título, subtítulo, frases, perguntas, números em destaque, ícones de redes sociais, nomes de perfil, logomarcas, assinaturas, marcas d'água ou molduras. Só são permitidos textos que façam parte natural da cena, como placas de edifícios.",
    "Mantenha margens de segurança para que os elementos principais não sejam cortados e a imagem continue compreensível em tamanho reduzido.",
    "Rostos naturais e sem deformações: preserve proporções anatômicas, traços faciais e texturas de pele, sem pele plastificada nem aparência genérica de imagem feita por inteligência artificial.",
    "Se não puder retratar essas pessoas, use silhuetas ou figuras de costas reconhecíveis pelo contexto e mantenha o resto da composição.",
]


def _pedido(path=None):
    p = Path(path or (ROOT / "pedido-artigo.json"))
    if not p.exists():
        raise SystemExit("faltou pedido-artigo.json")
    return json.loads(p.read_text(encoding="utf-8")), p


def _aprendizados():
    d = C.ler_json(ROOT / "artigos" / "aprendizados.json") or {}
    return [i.get("regra") for i in (d.get("itens") or []) if i.get("regra")]


def _fontes_pedido(ped):
    pauta = ped.get("pauta") or ped.get("atual") or {}
    fontes = pauta.get("fontes") or pauta.get("sources") or ped.get("fontes") or []
    extra = []
    for s in fontes:
        url = (s.get("url") if isinstance(s, dict) else None) or (s if isinstance(s, str) and s.startswith("http") else None)
        if url:
            pag = C.ler_pagina(url)
            if pag:
                extra.append(pag)
    return fontes, extra


def _prompt_sistema(modo):
    voz = C.texto(ROOT / "prompts" / "voz-editorial.md")
    estilo = C.texto(ROOT / "prompts" / "estilo-imagem-artigo.md")
    aprendiz = _aprendizados()
    base = f"""Você escreve, em nome de Rogerio Pires, um artigo de opinião para a Revista Timeline. Rogerio é jornalista, professor e comentarista político no Rio, linha de direita conservadora, crítica ao governo Lula, ao PT, ao ativismo do STF e à grande mídia. Responda SOMENTE com um objeto JSON, sem markdown.

VOZ EDITORIAL:
{voz}

APRENDIZADOS DO ROGERIO (aplique, salvo se violarem segurança jurídica):
{json.dumps(aprendizados := aprendiz, ensure_ascii=False)}

Nunca invente fato, número, data, citação ou url. Só use fontes e páginas dos insumos. Nunca afirme crime sem condenação. Português do Brasil. O ÚNICO travessão permitido é o da abertura ("RIO DE JANEIRO, … — "). Fora dela, vírgula, ponto ou dois-pontos.

O script preenche a linha de abertura (dateline) e o parágrafo final (apresentação). Você escreve o CORPO entre os dois.
"""
    if modo == "prompt_imagem":
        return base + f"""
MODO: só o prompt da imagem. Devolva {{"prompt_imagem": "..."}} a partir do texto atual. Não reescreva o artigo.

PADRÃO DA IMAGEM:
{estilo}

O prompt começa exatamente por: {INICIO_PROMPT}
Se houver pessoa real, a primeira linha é "ANEXE ANTES DE ENVIAR: uma foto nítida, recente e de frente de <nome> (sugestão: foto oficial do ...)." Depois linha em branco e o prompt, e inclua a frase de referência de identidade. Sem travessão. 1200 x 630, fotojornalismo cinematográfico.
"""
    if modo == "reescrever":
        return base + f"""
MODO: reescrita. Devolva {{"titulos": [5 pares {{titulo, subtitulo}}], "texto_corpo": "parágrafos separados por linha em branco, SEM a linha RIO DE JANEIRO e SEM a apresentação final", "palavras_chave": "5 ou mais, separadas por vírgula", "fontes": [{{name,url}} só urls fornecidas], "prompt_imagem": "...", "aprendizados_novos": [0 a 3 {{regra, comentario}}] ou []}}.

EDIÇÕES DO ROGERIO SÃO INTOCÁVEIS: título e subtítulo do "atual" ficam iguais, salvo se o feedback pedir para mudá-los. Cada trecho de editado.paragrafos entra no novo texto exatamente como está. O que o feedback não citar, preserve.

O primeiro par de titulos é o título e o subtítulo do atual (preservados), salvo pedido expresso de mudança. texto_corpo: 600 a 900 palavras no artigo completo (incluindo abertura e apresentação, cerca de 40 palavras). Primeira pessoa. Sem intertítulos, negrito ou listas. No máximo duas atribuições a veículos. Storytelling a partir de fatos verificáveis, nunca personagem inventado.

PADRÃO DA IMAGEM:
{estilo}
Prompt começa por: {INICIO_PROMPT}
"""
    return base + f"""
MODO: artigo novo. Devolva {{"titulos": [5 pares {{titulo, subtitulo}} ângulos distintos no padrão Jake Thomas], "texto_corpo": "parágrafos separados por linha em branco, SEM a linha RIO DE JANEIRO e SEM a apresentação final", "palavras_chave": "5 ou mais, do mais buscado ao mais específico", "fontes": [{{name,url}} só urls fornecidas], "prompt_imagem": "..."}}.

Títulos curtos, com lacuna de curiosidade, sem travessão, abrangentes. texto_corpo em primeira pessoa, 600 a 900 palavras no artigo completo (abertura + corpo + apresentação). Tom combativo e jornalisticamente sólido. Posição sempre de direita, sem falsa equivalência. No máximo duas atribuições a veículos no texto. Sem intertítulos, negrito ou listas.

PADRÃO DA IMAGEM:
{estilo}
Prompt começa por: {INICIO_PROMPT}
"""


def _fechar_texto(corpo, agora):
    corpo = (corpo or "").strip()
    # tira dateline/BIO se o modelo os colocou
    if corpo.startswith("RIO DE JANEIRO"):
        corpo = re.sub(r"^RIO DE JANEIRO,[^\n—]*—\s*", "", corpo, count=1).strip()
    if corpo.endswith(BIO):
        corpo = corpo[: -len(BIO)].rstrip()
    abertura = C.abertura_dateline(agora)
    texto = abertura + corpo
    if not texto.endswith(BIO):
        if texto and not texto.endswith("\n\n"):
            texto = texto.rstrip() + "\n\n"
        texto = texto + BIO
    return texto


def _completar_prompt(pi):
    pi = C.sem_travessao((pi or "").strip(), preservar_abertura=False)
    if not pi:
        return ""
    if "fotojornalismo cinematogr" not in pi.lower() or "1200" not in pi:
        # garante o começo padrão se o modelo esqueceu
        if not pi.lower().startswith("anexe antes de enviar") and not pi.lower().startswith("crie uma imagem horizontal"):
            pi = INICIO_PROMPT + " " + pi
    for frase in FRASES_OBRIG:
        if frase[:40].lower() not in pi.lower():
            pi = pi.rstrip() + " " + frase
    if "anexe antes de enviar" in pi.lower() and "referência de identidade" not in pi.lower() and "referencia de identidade" not in pi.lower().replace("ê", "e"):
        pi += ' Use a foto anexada como referência de identidade e preserve o rosto exatamente como é: formato do rosto e do queixo, olhos, sobrancelhas, nariz, boca, orelhas, linha do cabelo, barba, rugas, pintas, cor da pele e idade aparente. Não embeleze, não rejuvenesça, não emagreça e não troque nenhum traço; mude só a luz, a roupa, a pose e o cenário.'
    return C.sem_travessao(pi)


def _titulos(lista, atual=None, preservar=True):
    pares = []
    for x in lista or []:
        if not isinstance(x, dict):
            continue
        tit = C.sem_travessao((x.get("titulo") or "").strip())
        sub = C.sem_travessao((x.get("subtitulo") or "").strip())
        if tit and sub:
            pares.append({"titulo": tit, "subtitulo": sub})
    if preservar and atual and atual.get("titulo"):
        par = {"titulo": atual.get("titulo"), "subtitulo": atual.get("subtitulo") or (pares[0]["subtitulo"] if pares else "")}
        pares = [par] + [p for p in pares if not (p["titulo"] == par["titulo"] and p["subtitulo"] == par["subtitulo"])]
    if len(pares) != 5:
        raise ValueError(f"precisa de 5 pares de título, veio {len(pares)}")
    return pares[:5]


def _validar_arquivo(path, ped_path=None):
    cmd = [sys.executable, str(ROOT / "tools" / "artigo_validar.py"), str(path)]
    if ped_path:
        cmd.append(str(ped_path))
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _voz(path):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "voz_check.py"), str(path)], cwd=str(ROOT), capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def modo_prompt_imagem(ped, agora):
    aid = ped["id"]
    path = ROOT / "artigos" / f"{aid}.json"
    atual = C.ler_json(path)
    if not atual:
        raise SystemExit(f"não achei artigos/{aid}.json")
    payload = {"atual": ped.get("atual") or {"titulo": atual.get("titulo"), "subtitulo": atual.get("subtitulo"), "texto": atual.get("texto")}}
    aviso = None
    ultimo = None
    for tentativa in range(1, 4):
        user = "Escreva só o prompt_imagem do artigo atual.\n\n" + json.dumps(payload, ensure_ascii=False)
        if aviso:
            user += "\n\nCORREÇÃO:\n" + aviso
        try:
            bruto = modelos_ia.completar(
                [{"role": "system", "content": _prompt_sistema("prompt_imagem")}, {"role": "user", "content": user}],
                schema_nome="prompt_imagem",
                max_tokens=2000,
                temperature=0.3,
                timeout=120,
            )
            pi = _completar_prompt(bruto.get("prompt_imagem") if isinstance(bruto, dict) else "")
            if not pi:
                raise ValueError("prompt_imagem vazio")
            doc = dict(atual)
            doc["prompt_imagem"] = pi
            doc["saved_at"] = agora.replace(microsecond=0).isoformat()
            C.gravar_json(path, doc)
            # validar só o prompt: o resto não muda
            codigo, texto = _validar_arquivo(path)
            if codigo != 0:
                raise ValueError(texto.strip())
        except (modelos_ia.ModelError, ValueError, json.JSONDecodeError) as e:
            ultimo = e
            aviso = str(e)
            print(f"tentativa {tentativa} falhou: {e}", file=sys.stderr)
            continue
        print(f"prompt_imagem atualizado em {path}")
        C.escrever_output(arquivo=str(path), id=aid)
        return path
    raise SystemExit(f"não consegui gerar o prompt da imagem: {ultimo}")


def _montar_doc(ped, bruto, agora, permitidas, existente=None, modo="novo"):
    atual = ped.get("atual") or {}
    fb = (ped.get("feedback") or "").lower()
    preservar = modo == "reescrever" and "títul" not in fb and "titul" not in fb
    pares = _titulos(bruto.get("titulos"), atual if preservar else None, preservar=preservar)
    texto = _fechar_texto(bruto.get("texto_corpo") or bruto.get("texto") or "", agora)
    n = len(texto.split())
    if n < 600 or n > 900:
        raise ValueError(f"artigo com {n} palavras (precisa 600 a 900)")
    if not texto.startswith("RIO DE JANEIRO, ") or not texto.rstrip().endswith(BIO):
        raise ValueError("abertura ou apresentação incorretas")
    corpo = texto.split(" — ", 1)[1] if " — " in texto[:80] else texto
    if "—" in corpo or "–" in corpo:
        raise ValueError("travessão no corpo")
    fontes = C.filtrar_fontes(bruto.get("fontes") or bruto.get("sources") or [], permitidas, minimo=1, maximo=8)
    if existente and existente.get("fontes"):
        # mantém as antigas e acrescenta novas
        vistos = {s.get("url") for s in existente["fontes"] if isinstance(s, dict)}
        merged = list(existente["fontes"])
        for s in fontes:
            if s["url"] not in vistos:
                merged.append(s)
                vistos.add(s["url"])
        fontes = merged
    if not fontes:
        raise ValueError("sem fonte válida")
    pi = _completar_prompt(bruto.get("prompt_imagem") or "")
    if not pi:
        raise ValueError("sem prompt_imagem")
    pk = C.sem_travessao((bruto.get("palavras_chave") or "").strip())
    if len([k for k in pk.split(",") if k.strip()]) < 5:
        raise ValueError("menos de 5 palavras-chave")
    aid = ped["id"]
    if modo == "reescrever" and existente:
        versao_ant = int(existente.get("versao") or 1)
        agora_iso = agora.replace(microsecond=0).isoformat()
        anteriores = list(existente.get("anteriores") or [])
        anteriores = [{
            "em": agora_iso,
            "titulo": atual.get("titulo") or existente.get("titulo"),
            "subtitulo": atual.get("subtitulo") or existente.get("subtitulo"),
            "texto": atual.get("texto") or existente.get("texto"),
        }] + anteriores
        doc = dict(existente)
        doc.update({
            "versao": versao_ant + 1,
            "anteriores": anteriores[:5],
            "revisoes": list(existente.get("revisoes") or []) + [{"em": agora_iso, "feedback": ped.get("feedback") or ""}],
            "saved_at": agora_iso,
            "titulos": pares,
            "escolha": 0,
            "titulo": pares[0]["titulo"],
            "subtitulo": pares[0]["subtitulo"],
            "texto": texto,
            "palavras_chave": pk,
            "fontes": fontes,
            "prompt_imagem": pi,
        })
        if existente.get("status") == "publicado":
            doc["status"] = "publicado"
            doc["link"] = existente.get("link") or ""
        return doc, bruto.get("aprendizados_novos") or []
    return {
        "id": aid,
        "criado": agora.replace(microsecond=0).isoformat(),
        "saved_at": None,
        "origem": ped.get("origem"),
        "pauta_titulo": (ped.get("pauta") or {}).get("titulo") or ped.get("pauta_titulo") or "",
        "titulos": pares,
        "escolha": 0,
        "titulo": pares[0]["titulo"],
        "subtitulo": pares[0]["subtitulo"],
        "texto": texto,
        "palavras_chave": pk,
        "fontes": fontes,
        "prompt_imagem": pi,
        "status": "rascunho",
        "link": "",
    }, []


def gerar(pedido_path=None, agora=None):
    ped, ped_path = _pedido(pedido_path)
    if not re.fullmatch(r"[a-z0-9-]{6,90}", ped.get("id") or ""):
        raise SystemExit("id de artigo inválido")
    agora = C.agora_sp(agora)
    modo = ped.get("modo") or "novo"
    if modo == "prompt_imagem":
        return modo_prompt_imagem(ped, agora)
    fontes_ped, paginas = _fontes_pedido(ped)
    permitidas = C.urls_de(fontes_ped, paginas, ped)
    existente = C.ler_json(ROOT / "artigos" / f"{ped['id']}.json")
    payload = {
        "pedido": {k: ped[k] for k in ped if k != "modo" or True},
        "paginas_lidas": paginas,
        "artigo_atual": None if modo != "reescrever" else {
            "origem": (existente or {}).get("origem"),
            "fontes": (existente or {}).get("fontes"),
            "status": (existente or {}).get("status"),
        },
    }
    aviso = None
    ultimo = None
    path = ROOT / "artigos" / f"{ped['id']}.json"
    path.parent.mkdir(exist_ok=True)
    for tentativa in range(1, 4):
        user = "Escreva o artigo com os insumos. Não invente fonte.\n\n" + json.dumps(payload, ensure_ascii=False)
        if aviso:
            user += "\n\nCORREÇÃO:\n" + aviso
        try:
            bruto = modelos_ia.completar(
                [{"role": "system", "content": _prompt_sistema(modo)}, {"role": "user", "content": user}],
                schema_nome="artigo",
                max_tokens=8000,
                temperature=0.4,
                timeout=180,
            )
            doc, novos = _montar_doc(ped, bruto, agora, permitidas, existente, modo=modo)
            C.gravar_json(path, doc)
            codigo, texto = _validar_arquivo(path, ped_path if modo == "reescrever" else None)
            if codigo != 0:
                raise ValueError(texto.strip() or "artigo_validar recusou")
            vc, vt = _voz(path)
            if vc != 0:
                raise ValueError("voz_check: " + vt.strip())
        except (modelos_ia.ModelError, ValueError, json.JSONDecodeError) as e:
            ultimo = e
            aviso = str(e)
            print(f"tentativa {tentativa} falhou: {e}", file=sys.stderr)
            continue
        if modo == "reescrever" and ped.get("aprender") and novos:
            (ROOT / "novos-aprendizados.json").write_text(
                json.dumps([{"regra": C.sem_travessao(str(x.get("regra", "")).strip()), "comentario": C.sem_travessao(str(x.get("comentario", "")).strip())} for x in novos if isinstance(x, dict) and x.get("regra")][:3], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"artigo gravado em {path}")
        C.escrever_output(arquivo=str(path), id=ped["id"])
        return path
    raise SystemExit(f"não consegui gerar o artigo: {ultimo}")


def _self_test():
    agora = C.agora_sp(__import__("datetime").datetime(2026, 11, 19, 10, 0, tzinfo=C.BRT))
    corpo = " ".join(["O fato está na mesa e o país precisa olhar para ele."] * 55)
    texto = _fechar_texto(corpo, agora)
    assert texto.startswith("RIO DE JANEIRO, 19 de novembro de 2026 — ")
    assert texto.rstrip().endswith(BIO)
    assert " — " in texto[:60]
    pi = _completar_prompt("Uma cena no Congresso à noite, retrato low-key.")
    assert "1200" in pi and "fotojornalismo cinematogr" in pi.lower()
    pares = _titulos([{"titulo": f"Título {i}", "subtitulo": f"Sub {i}"} for i in range(5)])
    assert len(pares) == 5
    ped = {"id": "teste-artigo-abc", "atual": {"titulo": "Título 0", "subtitulo": "Sub 0"}, "modo": "reescrever", "feedback": "Deixe mais curto."}
    bruto = {"titulos": pares, "texto_corpo": corpo, "palavras_chave": "a, b, c, d, e", "fontes": [{"name": "A", "url": "https://ex.com/a"}], "prompt_imagem": pi}
    doc, _ = _montar_doc(ped, bruto, agora, {"https://ex.com/a"}, existente={"id": "teste-artigo-abc", "versao": 1, "titulo": "Título 0", "subtitulo": "Sub 0", "texto": "velho", "fontes": [{"name": "A", "url": "https://ex.com/a"}], "status": "rascunho", "criado": "2026-11-01T10:00:00-03:00", "origem": {}, "pauta_titulo": "x"}, modo="reescrever")
    assert doc["versao"] == 2 and doc["titulo"] == "Título 0"
    print("gerar_artigo: ok")


def main():
    if "--self-test" in sys.argv:
        _self_test()
        return
    path = None
    if "--pedido" in sys.argv:
        path = sys.argv[sys.argv.index("--pedido") + 1]
    gerar(pedido_path=path)


if __name__ == "__main__":
    main()

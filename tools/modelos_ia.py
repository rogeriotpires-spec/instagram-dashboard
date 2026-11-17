"""Cliente de chat para a redação das edições.

Um só lugar para os fluxos que saírem do Claude. Esta versão atende a edição
de notícias. Os outros fluxos (pautas, programa, análises, quentes, artigo,
sob demanda) ainda não usam este módulo.

Provedor:
- Se OPENAI_API_KEY estiver definida, as chamadas vão para
  https://api.openai.com/v1 com o modelo OPENAI_MODEL (padrão gpt-4.1) e,
  se estourar taxa ou contexto, OPENAI_MODEL_MINI (padrão gpt-4.1-mini).
- Senão, usa o GitHub Models em https://models.github.ai/inference
  (OpenAI-compatível), com GITHUB_TOKEN e o modelo openai/gpt-4.1
  (reserva openai/gpt-4.1-mini). Permissão da action: models: read.

A chave e o token não são impressos. Corpo de erro é cortado e mascarado.
"""
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

class ModelError(Exception):
    def __init__(self, msg, *, status=None, rate=False, tamanho=False, esquema=False, fatal=False):
        super().__init__(msg)
        self.status = status
        self.rate = rate
        self.tamanho = tamanho
        self.esquema = esquema
        self.fatal = fatal


_estado = {"modelos": [], "ultima": 0.0, "uso": []}


def _redigir(texto):
    s = str(texto or "")
    s = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", s)
    s = re.sub(r"ghs_[A-Za-z0-9_]{8,}", "ghs_***", s)
    s = re.sub(r"github_pat_[A-Za-z0-9_]{8,}", "github_pat_***", s)
    s = re.sub(r"Bearer [A-Za-z0-9._\-]+", "Bearer ***", s)
    return s[:400]


def configuracao():
    """Devolve base_url, chave, modelo principal, modelo reserva e rótulo do provedor."""
    openai = (os.environ.get("OPENAI_API_KEY") or "").strip()
    # Não imprime a chave. Só diz se ela chegou ao processo.
    print(f"chave_openai={'sim' if openai else 'nao'}", file=sys.stderr)
    if openai:
        base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        return {
            "provedor": "openai",
            "base": base,
            "chave": openai,
            "modelo": os.environ.get("OPENAI_MODEL") or "gpt-4.1",
            "mini": os.environ.get("OPENAI_MODEL_MINI") or "gpt-4.1-mini",
        }
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_MODELS_TOKEN") or "").strip()
    if not token:
        raise ModelError("sem GITHUB_TOKEN nem OPENAI_API_KEY para chamar o modelo", fatal=True)
    base = (os.environ.get("GITHUB_MODELS_URL") or "https://models.github.ai/inference").rstrip("/")
    return {
        "provedor": "github",
        "base": base,
        "chave": token,
        "modelo": os.environ.get("GITHUB_MODELS_MODEL") or "openai/gpt-4.1",
        "mini": os.environ.get("GITHUB_MODELS_MODEL_MINI") or "openai/gpt-4.1-mini",
    }


def modelos_usados():
    return list(dict.fromkeys(_estado["modelos"]))


def _espera():
    gap = float(os.environ.get("IA_INTERVAL") or "2")
    falta = gap - (time.time() - _estado["ultima"])
    if falta > 0:
        time.sleep(falta)
    _estado["ultima"] = time.time()


def _parse_corpo(corpo, ctype):
    texto = corpo.decode("utf-8", "replace").strip()
    baixo = texto.lower()
    if not texto or baixo in ("ok", "ok.", "healthy") or (ctype or "").startswith("text/plain"):
        raise ModelError(
            "o endpoint de modelos não devolveu uma conclusão (resposta textual curta; "
            "GitHub Models foi desligado em 30/07/2026 e models.github.ai responde OK)",
            fatal=False,
        )
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as e:
        raise ModelError(f"JSON inválido do modelo: {_redigir(texto)[:180]} ({e})")
    if isinstance(dados, dict) and dados.get("error"):
        err = dados["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        code = err.get("code") if isinstance(err, dict) else None
        baixo = (msg or "").lower()
        raise ModelError(
            _redigir(msg),
            rate="rate" in baixo or code == "rate_limit_exceeded",
            tamanho=any(k in baixo for k in ("context", "maximum", "too large", "token")),
        )
    try:
        escolha = dados["choices"][0]
        conteudo = escolha["message"]["content"]
        fim = escolha.get("finish_reason")
    except (KeyError, IndexError, TypeError) as e:
        raise ModelError(f"formato inesperado: {_redigir(texto)[:180]} ({e})")
    if isinstance(conteudo, list):
        conteudo = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in conteudo)
    if fim == "length":
        raise ModelError("resposta cortada pelo limite de tokens", tamanho=True)
    return conteudo or ""


def _anotar_uso(corpo, modelo):
    try:
        dados = json.loads(corpo.decode("utf-8"))
        uso = dados.get("usage") or {}
        prompt = int(uso.get("prompt_tokens") or 0)
        saida = int(uso.get("completion_tokens") or 0)
    except (ValueError, TypeError, AttributeError):
        return
    _estado["uso"].append({"modelo": modelo, "prompt": prompt, "saida": saida})


def custo_usd():
    """Preço de tabela da OpenAI por 1 milhão de tokens, sem cache."""
    tabela = {
        "gpt-4.1": (2.0, 8.0),
        "gpt-4.1-mini": (0.40, 1.60),
    }
    total = 0.0
    por = {}
    for u in _estado.get("uso") or []:
        nome = (u.get("modelo") or "").split("/")[-1]
        entrada, saida = tabela.get(nome, (2.0, 8.0))
        valor = u["prompt"] / 1_000_000 * entrada + u["saida"] / 1_000_000 * saida
        total += valor
        bloco = por.setdefault(nome, {"prompt": 0, "saida": 0, "usd": 0.0})
        bloco["prompt"] += u["prompt"]
        bloco["saida"] += u["saida"]
        bloco["usd"] += valor
    return round(total, 4), por


def extrair_json(conteudo):
    s = (conteudo or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            return json.loads(s[i:j + 1])
        raise


def _post(url, chave, payload, timeout):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + chave,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        corpo = e.read() if hasattr(e, "read") else b""
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", corpo, dict(e.headers or {})


def _classificar_http(status, corpo, headers):
    texto = _redigir(corpo.decode("utf-8", "replace"))
    if status == 429:
        ra = headers.get("Retry-After") or headers.get("retry-after") or ""
        return ModelError(f"HTTP 429 {texto[:160]}", status=429, rate=True), ra
    if status in (400, 413) and any(k in texto.lower() for k in ("context", "maximum", "too many", "token", "length")):
        return ModelError(f"HTTP {status} contexto {texto[:160]}", status=status, tamanho=True), ""
    if status == 400 and any(k in texto.lower() for k in ("response_format", "json_schema", "schema")):
        return ModelError(f"HTTP 400 schema {texto[:160]}", status=400, esquema=True), ""
    if status in (401, 403):
        return ModelError(f"HTTP {status} autenticação recusada pelo provedor de modelos", status=status, fatal=True), ""
    return ModelError(f"HTTP {status} {texto[:200]}", status=status), ""


def completar(mensagens, *, schema=None, schema_nome="resposta", max_tokens=4000, temperature=0.2, timeout=120, barato=False):
    """Devolve o objeto JSON da resposta. Levanta ModelError se os dois modelos falharem.

    barato=True começa pelo modelo menor (gpt-4.1-mini). A redação da matéria fica no modelo cheio.
    """
    cfg = configuracao()
    url = cfg["base"] + "/chat/completions"
    ordem = []
    fila = (cfg["mini"], cfg["modelo"]) if barato else (cfg["modelo"], cfg["mini"])
    for m in fila:
        if m not in ordem:
            ordem.append(m)
    erros = []
    for modelo in ordem:
        usar_schema = bool(schema)
        for tentativa in range(3):
            payload = {
                "model": modelo,
                "messages": mensagens,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if usar_schema and schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema_nome, "strict": True, "schema": schema},
                }
            else:
                payload["response_format"] = {"type": "json_object"}
            _espera()
            try:
                status, ctype, corpo, headers = _post(url, cfg["chave"], payload, timeout)
            except Exception as e:
                erros.append(ModelError(f"rede {type(e).__name__}: {_redigir(e)}"))
                time.sleep(min(20, 1.5 * (2 ** tentativa)) + random.uniform(0, 0.4))
                continue
            if status != 200:
                err, retry_after = _classificar_http(status, corpo, headers)
                erros.append(err)
                if err.fatal:
                    raise err
                if err.esquema and usar_schema:
                    usar_schema = False
                    continue
                if err.rate or err.tamanho or status >= 500:
                    if err.tamanho and modelo == cfg["mini"]:
                        break
                    espera = 0
                    if retry_after and str(retry_after).replace(".", "", 1).isdigit():
                        espera = min(20, float(retry_after))
                    else:
                        espera = min(20, 1.5 * (2 ** tentativa)) + random.uniform(0.2, 0.8)
                    time.sleep(espera)
                    if err.tamanho or (err.rate and tentativa == 2):
                        break
                    continue
                break
            try:
                _anotar_uso(corpo, modelo)
                conteudo = _parse_corpo(corpo, ctype)
                dados = extrair_json(conteudo)
            except ModelError as e:
                erros.append(e)
                if e.tamanho:
                    break
                if "não devolveu uma conclusão" in str(e):
                    # endpoint aposentado ou healthcheck: não adianta insistir no mesmo modelo
                    break
                continue
            if not isinstance(dados, dict):
                erros.append(ModelError("JSON do modelo não é objeto"))
                continue
            _estado["modelos"].append(modelo)
            return dados
    raise ModelError("falha ao chamar o modelo: " + " | ".join(str(e) for e in erros[-4:]))


def _self_test():
    ok = _parse_corpo(
        json.dumps({"choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}]}).encode(),
        "application/json",
    )
    assert extrair_json(ok) == {"a": 1}
    assert extrair_json("```json\n{\"b\": 2}\n```") == {"b": 2}
    try:
        _parse_corpo(b"OK", "text/plain")
        raise SystemExit("deveria recusar o OK do endpoint aposentado")
    except ModelError as e:
        assert "não devolveu" in str(e)
    mascarado = _redigir("Bearer ghs_abcdefghijklmnop e sk-proj-abcdefghij")
    assert "ghs_abcdefghijklmnop" not in mascarado and "sk-proj" not in mascarado
    print("modelos_ia: ok")


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print("uso: python3 tools/modelos_ia.py --self-test", file=sys.stderr)
        sys.exit(2)

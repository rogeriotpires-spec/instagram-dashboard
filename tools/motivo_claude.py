"""Mostra no log do GitHub o motivo de uma falha do Claude Code Action.

Lê o arquivo de execução (claude-execution-output.json) e imprime só a
mensagem final (o resultado e o texto de erro), sem o resto da conversa.
Uso: python3 tools/motivo_claude.py [caminho_do_execution_file]
"""
import json
import os
import sys

PADRAO = "/home/runner/work/_temp/claude-execution-output.json"

DICAS = [
    (("max turns", "max_turns", "error_max_turns"),
     "O Claude esgotou o número máximo de passos antes de terminar."),
    (("usage limit", "limit reached", "rate limit", "429", "quota"),
     "Limite de uso da conta Claude atingido. Espere o limite renovar e gere de novo."),
    (("invalid api key", "authentication", "oauth", "401", "403", "expired", "/login"),
     "Credencial do Claude recusada. Renove o secret CLAUDE_CODE_OAUTH_TOKEN no repositório."),
    (("overloaded", "529", "500", "internal server", "timeout", "timed out"),
     "Serviço do Claude instável no momento. Tentar de novo em alguns minutos costuma resolver."),
]


def main():
    caminho = (sys.argv[1] if len(sys.argv) > 1 else "") or PADRAO
    if not os.path.exists(caminho):
        caminho = PADRAO
    try:
        dados = json.load(open(caminho, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"::warning::Não consegui ler o registro do Claude ({e}).")
        return
    msgs = dados if isinstance(dados, list) else [dados]

    textos = []
    resultado = None
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if m.get("type") == "result":
            resultado = m
        if m.get("type") == "assistant":
            for c in (m.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                    textos.append(c["text"])

    partes = []
    if resultado:
        print(f"subtype={resultado.get('subtype')} is_error={resultado.get('is_error')} "
              f"turnos={resultado.get('num_turns')} custo_usd={resultado.get('total_cost_usd')}")
        for k in ("result", "error", "errors", "api_error_status"):
            v = resultado.get(k)
            if v:
                partes.append(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    if textos:
        partes.append(textos[-1])

    motivo = " | ".join(p.strip() for p in partes if p and str(p).strip())[:1500]
    if not motivo:
        motivo = "o Claude não deixou mensagem de erro no registro"
    print(f"::error::Motivo informado pelo Claude: {motivo}")

    baixo = motivo.lower()
    for chaves, dica in DICAS:
        if any(k in baixo for k in chaves):
            print(f"::notice::{dica}")
            break


if __name__ == "__main__":
    main()

/* Botões de ação do Painel @rogerrio (Atualizar, Gerar edição, Gerar pauta, Virar pauta).
   Com o intermediário (Cloudflare Worker) configurado em API, tudo funciona com um clique.
   Sem ele, o botão abre a página do GitHub como antes. */
window.Painel = (() => {
  const API = "https://painel.rogeriotpires.workers.dev"; // intermediário na Cloudflare
  const CLAUDE = false;    // true quando o token da assinatura estiver no GitHub
  const REPO = "rogeriotpires-spec/instagram-dashboard";
  const GH = `https://api.github.com/repos/${REPO}`;
  const ls = {
    get: k => { try { return localStorage.getItem(k) } catch (e) { return null } },
    set: (k, v) => { try { localStorage.setItem(k, v) } catch (e) {} },
    del: k => { try { localStorage.removeItem(k) } catch (e) {} },
  };
  let mem = null;
  const key = ask => {
    if (!ask) { const k = mem || ls.get("painel_key"); if (k) return k }
    const k = prompt(ask === "retry" ? "Senha incorreta. Digite a senha do painel:" : "Senha do painel (pedida só na primeira vez neste aparelho):");
    if (k) { mem = k; ls.set("painel_key", k) }
    return k;
  };
  async function call(path, data) {
    let k = key();
    for (let i = 0; i < 3; i++) {
      if (!k) throw new Error("Senha não informada.");
      const r = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json", "X-Painel-Key": k }, body: JSON.stringify(data || {}) });
      const j = await r.json().catch(() => ({}));
      if (r.status === 401) { ls.del("painel_key"); mem = null; k = key("retry"); continue }
      if (!r.ok) throw new Error(j.error || `Erro ${r.status}`);
      return j;
    }
    throw new Error("Senha incorreta.");
  }
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const lastRun = wf => fetch(`${GH}/actions/workflows/${wf}/runs?per_page=1`, { cache: "no-store" })
    .then(r => r.ok ? r.json() : null).then(j => j && j.workflow_runs && j.workflow_runs[0]).catch(() => null);

  /* Acompanha a execução: espera o GitHub terminar e o site publicar o arquivo novo. */
  async function track({ wf, since, stamp, say, minutes = 12, labels = {} }) {
    const base = await stamp().catch(() => null), t0 = Date.now();
    let finished = false;
    while (Date.now() - t0 < minutes * 60e3) {
      await sleep(finished ? 10000 : 20000);
      if (!finished) {
        const run = await lastRun(wf);
        if (!run || new Date(run.created_at) < since) { say(labels.wait || "Aguardando o GitHub começar…"); continue }
        if (run.status !== "completed") { say(labels.running || "Em andamento…"); continue }
        if (run.conclusion !== "success") { say("Não deu certo desta vez. Tente de novo em alguns minutos."); return false }
        finished = true;
      }
      say("Pronto no GitHub. Publicando no site…");
      const s = await stamp().catch(() => null);
      if (s && s !== base) { say("Atualizado! Recarregando…"); await sleep(800); location.reload(); return true }
    }
    say("Está demorando mais que o normal. Recarregue a página em alguns minutos.");
    return false;
  }

  /* Liga um botão a uma ação. */
  function wire({ btn, st, job, wf, stamp, minutes, labels, needsClaude }) {
    if (!btn) return;
    if (needsClaude && !(API && CLAUDE)) { btn.hidden = true; return }
    const say = t => { if (st) st.textContent = t };
    btn.onclick = async () => {
      const since = new Date(Date.now() - 90e3);
      btn.disabled = true;
      try {
        if (API) {
          say("Enviando…");
          const j = await call("/run", { job });
          say(j.already ? "Já estava em andamento. Acompanhando…" : (labels && labels.running) || "Em andamento…");
        } else {
          window.open(`https://github.com/${REPO}/actions/workflows/${wf}`, "_blank", "noopener");
          say("Na página do GitHub, clique em “Run workflow” e confirme no botão verde.");
        }
        await track({ wf, since, stamp, say, minutes, labels });
      } catch (e) { say(e.message) }
      btn.disabled = false;
    };
    lastRun(wf).then(run => {
      if (run && run.status !== "completed" && Date.now() - new Date(run.created_at) < minutes * 60e3) {
        btn.disabled = true; say((labels && labels.running) || "Em andamento…");
        track({ wf, since: new Date(run.created_at), stamp, say, minutes, labels }).finally(() => btn.disabled = false);
      }
    });
  }

  return { API, CLAUDE, REPO, call, wire, track, lastRun };
})();

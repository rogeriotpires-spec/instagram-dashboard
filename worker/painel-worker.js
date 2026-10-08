// Intermediário do Painel @rogerrio (Cloudflare Worker).
// Recebe os cliques do site e aciona o GitHub com um token guardado aqui,
// fora do site público. Segredos configurados na Cloudflare (nunca no código):
//   GH_TOKEN    token do GitHub (fine-grained: Actions e Issues com leitura e escrita)
//   PAINEL_KEY  senha do painel, digitada uma vez no navegador
const REPO = "rogeriotpires-spec/instagram-dashboard";
const ORIGINS = ["https://rogeriotpires-spec.github.io"];
const JOBS = {
  coleta: "coleta-instagram.yml",
  noticias: "noticias.yml",
  pautas: "pautas.yml",
  sobdemanda: "sob-demanda.yml",
  programa: "programa.yml",
};
// Ações novas não exigem trocar este código: um nome simples (letras, números e hífen)
// aciona o arquivo .github/workflows/<nome>.yml, se ele existir no repositório.
const wfFor = (job) => JOBS[job] || (/^[a-z0-9-]{3,40}$/.test(job) ? `${job}.yml` : null);

export default {
  async fetch(req, env) {
    const origin = req.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, X-Painel-Key",
      "Vary": "Origin",
    };
    const json = (o, s = 200) =>
      new Response(JSON.stringify(o), { status: s, headers: { ...cors, "Content-Type": "application/json" } });
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });

    const url = new URL(req.url);
    if (url.pathname === "/ping") return json({ ok: true });
    if (req.method !== "POST") return json({ error: "Use POST." }, 405);
    if (!env.PAINEL_KEY || req.headers.get("X-Painel-Key") !== env.PAINEL_KEY)
      return json({ error: "senha" }, 401);
    if (!env.GH_TOKEN) return json({ error: "GH_TOKEN não configurado na Cloudflare." }, 500);

    const gh = (path, init = {}) =>
      fetch(`https://api.github.com/repos/${REPO}${path}`, {
        ...init,
        headers: {
          Authorization: `Bearer ${env.GH_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "painel-rogerrio",
          "Content-Type": "application/json",
        },
      });
    const dispatch = async (job) => {
      const wf = wfFor(job);
      if (!wf) return { status: 400, body: { error: "Ação desconhecida." } };
      const r = await gh(`/actions/workflows/${wf}/runs?per_page=1`);
      if (r.status === 404) return { status: 404, body: { error: "Essa ação ainda não foi instalada." } };
      const last = r.ok ? ((await r.json()).workflow_runs || [])[0] : null;
      if (last && last.status !== "completed")
        return { status: 200, body: { ok: true, already: true, started: last.created_at } };
      const d = await gh(`/actions/workflows/${wf}/dispatches`, { method: "POST", body: JSON.stringify({ ref: "main" }) });
      if (d.status === 204) return { status: 200, body: { ok: true, started: new Date().toISOString() } };
      return { status: 502, body: { error: `GitHub respondeu ${d.status}`, detail: (await d.text()).slice(0, 300) } };
    };

    const input = await req.json().catch(() => ({}));

    if (url.pathname === "/run") {
      const r = await dispatch(String(input.job || ""));
      return json(r.body, r.status);
    }

    if (url.pathname === "/pauta") {
      const title = String(input.title || "").slice(0, 200).trim();
      const body = String(input.body || "").slice(0, 8000);
      if (!title) return json({ error: "Título vazio." }, 400);
      const r = await gh(`/issues`, {
        method: "POST",
        body: JSON.stringify({ title: `[Virar pauta] ${title}`, body }),
      });
      if (!r.ok) return json({ error: `GitHub respondeu ${r.status}` }, 502);
      const issue = await r.json(); // a abertura da issue dispara a geração da pauta no GitHub
      return json({ ok: true, issue: issue.number });
    }

    return json({ error: "Rota desconhecida." }, 404);
  },
};

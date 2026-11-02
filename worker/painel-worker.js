// Intermediário do Painel @rogerrio (Cloudflare Worker).
// Recebe os cliques do site e aciona o GitHub com um token guardado aqui,
// fora do site público. Segredos configurados na Cloudflare (nunca no código):
//   GH_TOKEN    token do GitHub (fine-grained: Actions e Issues com leitura e escrita)
//   PAINEL_KEY  senha do painel, digitada uma vez no navegador
// Agenda (horários das edições, geridos na página agenda.html do painel):
//   AGENDA      armazenamento KV ligado a este Worker (guarda os horários e o que já foi disparado)
//   Cron Trigger "*/5 * * * *" (a cada 5 minutos): o Worker confere a agenda e aciona o GitHub no minuto certo
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

// ---------- Agenda: horários das edições, editados na página agenda.html ----------
const TZ = "America/Sao_Paulo";
const AGENDAVEIS = { noticias: "Edições de notícias" }; // ações que a agenda pode disparar
const JANELA_MIN = 20; // se um tique de 5 em 5 minutos falhar, ainda dispara até 20 minutos depois do horário
const TODOS_DIAS = [0, 1, 2, 3, 4, 5, 6]; // 0 = domingo
const PADRAO = [
  { id: "ed-manha", job: "noticias", hora: "05:00", dias: TODOS_DIAS, ativo: true },
  { id: "ed-meio-dia", job: "noticias", hora: "11:00", dias: TODOS_DIAS, ativo: true },
  { id: "ed-noite", job: "noticias", hora: "18:00", dias: TODOS_DIAS, ativo: true },
];
const agoraSP = (d = new Date()) => {
  const p = new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", weekday: "short", hourCycle: "h23",
  }).formatToParts(d);
  const g = (t) => p.find((x) => x.type === t).value;
  return {
    data: `${g("year")}-${g("month")}-${g("day")}`,
    dia: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(g("weekday")),
    min: Number(g("hour")) * 60 + Number(g("minute")),
  };
};
const hm = (h) => Number(h.slice(0, 2)) * 60 + Number(h.slice(3, 5));
const limparAgenda = (arr) => {
  if (!Array.isArray(arr) || arr.length > 24) throw new Error("Lista de horários inválida (máximo 24).");
  const ids = new Set();
  return arr.map((x, i) => {
    const job = String(x && x.job || "");
    if (!AGENDAVEIS[job]) throw new Error("Ação não permitida na agenda.");
    const hora = String(x.hora || "");
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(hora)) throw new Error(`Horário inválido na linha ${i + 1}.`);
    const dias = [...new Set((Array.isArray(x.dias) ? x.dias : []).map(Number))].filter((d) => Number.isInteger(d) && d >= 0 && d <= 6).sort();
    if (!dias.length) throw new Error(`Escolha ao menos um dia na linha ${i + 1}.`);
    let id = /^[a-z0-9-]{2,24}$/.test(String(x.id || "")) ? String(x.id) : "h" + Math.random().toString(36).slice(2, 8);
    while (ids.has(id)) id = "h" + Math.random().toString(36).slice(2, 8);
    ids.add(id);
    return { id, job, hora, dias, ativo: x.ativo !== false };
  });
};
const lerAgenda = async (env) => {
  const t = await env.AGENDA.get("config");
  if (!t) return PADRAO;
  try { return limparAgenda(JSON.parse(t)); } catch (e) { return PADRAO; }
};
const lerLog = async (env) => {
  try { return JSON.parse((await env.AGENDA.get("log")) || "[]"); } catch (e) { return []; }
};
const anotar = async (env, evento) => {
  const log = await lerLog(env);
  log.unshift({ quando: new Date().toISOString(), ...evento });
  await env.AGENDA.put("log", JSON.stringify(log.slice(0, 30)));
};
const ghx = (env, path, init = {}) =>
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
// Dispara o workflow da agenda, sem duplicar se já houver uma geração em andamento.
const dispararAgendado = async (env, job) => {
  const wf = JOBS[job];
  const r = await ghx(env, `/actions/workflows/${wf}/runs?per_page=1`);
  const last = r.ok ? ((await r.json()).workflow_runs || [])[0] : null;
  if (last && last.status !== "completed" && Date.now() - new Date(last.created_at) < 60 * 60e3)
    return { ok: true, resultado: "já em andamento, não duplicou" };
  const d = await ghx(env, `/actions/workflows/${wf}/dispatches`, {
    method: "POST", body: JSON.stringify({ ref: "main", inputs: { agendado: "sim" } }),
  });
  if (d.status === 204) return { ok: true, resultado: "disparado" };
  return { ok: false, resultado: `GitHub respondeu ${d.status}: ${(await d.text()).slice(0, 160)}` };
};
const rodarAgenda = async (env) => {
  if (!env.AGENDA || !env.GH_TOKEN) return;
  const itens = await lerAgenda(env);
  const n = agoraSP();
  for (const it of itens) {
    if (!it.ativo || !it.dias.includes(n.dia)) continue;
    const alvo = hm(it.hora);
    if (n.min < alvo || n.min >= alvo + JANELA_MIN) continue;
    if ((await env.AGENDA.get(`ult:${it.id}`)) === n.data) continue;
    await env.AGENDA.put(`ult:${it.id}`, n.data, { expirationTtl: 172800 }); // marca antes, para nunca disparar duas vezes
    let res;
    try { res = await dispararAgendado(env, it.job); } catch (e) { res = { ok: false, resultado: "erro: " + String(e).slice(0, 120) }; }
    await anotar(env, { id: it.id, job: it.job, hora: it.hora, ok: res.ok, resultado: res.resultado });
  }
};

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
    const dispatch = async (job, inputs) => {
      const wf = wfFor(job);
      if (!wf) return { status: 400, body: { error: "Ação desconhecida." } };
      const r = await gh(`/actions/workflows/${wf}/runs?per_page=1`);
      if (r.status === 404) return { status: 404, body: { error: "Essa ação ainda não foi instalada." } };
      const last = r.ok ? ((await r.json()).workflow_runs || [])[0] : null;
      if (!inputs && last && last.status !== "completed")
        return { status: 200, body: { ok: true, already: true, started: last.created_at } };
      const d = await gh(`/actions/workflows/${wf}/dispatches`, { method: "POST", body: JSON.stringify(inputs ? { ref: "main", inputs } : { ref: "main" }) });
      if (d.status === 204) return { status: 200, body: { ok: true, started: new Date().toISOString() } };
      return { status: 502, body: { error: `GitHub respondeu ${d.status}`, detail: (await d.text()).slice(0, 300) } };
    };

    const input = await req.json().catch(() => ({}));

    if (url.pathname === "/run") {
      const r = await dispatch(String(input.job || ""));
      return json(r.body, r.status);
    }

    if (url.pathname === "/acao") {
      // ação com dados (ex.: gerar ou salvar artigo da Timeline): aciona o workflow com o campo "payload"
      const payload = typeof input.payload === "string" ? input.payload : JSON.stringify(input.payload || {});
      if (payload.length > 60000) return json({ error: "Texto grande demais para enviar de uma vez." }, 413);
      const r = await dispatch(String(input.job || ""), { payload });
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

    if (url.pathname === "/avulso") {
      // item avulso para a pauta do programa: um site ou uma frase a pesquisar
      const pedido = String(input.pedido || "").slice(0, 1500).trim();
      const data = String(input.data || "").slice(0, 10);
      const obs = String(input.obs || "").slice(0, 1500).trim();
      if (!pedido) return json({ error: "Escreva um site ou uma frase." }, 400);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(data)) return json({ error: "Data inválida." }, 400);
      const body = `Pedido de item avulso para a pauta do programa.\n\nData da pauta: ${data}\nPedido: ${pedido}\nObservação: ${obs || "(nenhuma)"}\n`;
      const r = await gh(`/issues`, {
        method: "POST",
        body: JSON.stringify({ title: `[Pauta avulsa] ${pedido.slice(0, 90)}`, body }),
      });
      if (!r.ok) return json({ error: `GitHub respondeu ${r.status}` }, 502);
      const issue = await r.json(); // a abertura da issue dispara a pesquisa no GitHub
      return json({ ok: true, issue: issue.number });
    }

    if (url.pathname === "/agenda/ler" || url.pathname === "/agenda/salvar") {
      if (!env.AGENDA) return json({ error: "Falta ligar o armazenamento AGENDA a este Worker na Cloudflare." }, 500);
      if (url.pathname === "/agenda/salvar") {
        let novos;
        try { novos = limparAgenda(input.itens); } catch (e) { return json({ error: e.message }, 400); }
        await env.AGENDA.put("config", JSON.stringify(novos));
        await anotar(env, { id: "-", job: "agenda", hora: "-", ok: true, resultado: "horários alterados no painel" });
      }
      const itens = await lerAgenda(env);
      const ultimos = {};
      for (const it of itens) ultimos[it.id] = (await env.AGENDA.get(`ult:${it.id}`)) || null;
      return json({ itens, ultimos, log: await lerLog(env), agora: agoraSP(), cron: !!env.AGENDA });
    }

    return json({ error: "Rota desconhecida." }, 404);
  },
  // Chamado pelo Cron Trigger a cada 5 minutos.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(rodarAgenda(env));
  },
};

# Edição de notícias do Painel @rogerrio

Usado pela tarefa agendada (6h, 12h, 19h) e pelo botão "Gerar edição agora" do site.
Você está na raiz do repositório rogeriotpires-spec/instagram-dashboard (branch main), já clonado.

Você edita a aba Notícias do painel de Rogerio Pires (@rogerrio; jornalista e comentarista político no Rio de Janeiro; linha de direita conservadora, crítica ao governo Lula, ao PT, ao ativismo do STF e à grande mídia). Página: https://rogeriotpires-spec.github.io/instagram-dashboard/noticias.html

## Qual edição
Use o horário atual de Brasília (TZ=America/Sao_Paulo): antes das 9h é a edição 06h; das 9h às 15h59 é a 12h; das 16h em diante é a 19h. Se o arquivo da edição já existir (geração manual repetida), reescreva-o.

## Segurança do repositório
NUNCA crie, altere ou apague arquivos fora de noticias/AAAA-MM-DD-HHh.json, noticias/index.json e pautas/sob-demanda.json. Scripts auxiliares ficam em /tmp, nunca dentro do repositório. Seja eficiente: o ideal é terminar em até 20 minutos.

## Passos
1. Leia noticias/config.json (critérios, pesos, linhas do resumo, itens por bloco, mínimo de portais, blocos, portais e campo editorial de cada um) e noticias/index.json. Se existir, leia a edição anterior mais recente em noticias/ (para medir persistência e manter ids estáveis).
2. Pesquise com WebSearch e WebFetch as homes e editorias dos portais de config.outlets nas últimas 12 horas (na edição das 06h, desde as 19h do dia anterior). Para cada fato relevante, descubra quais portais da lista o publicaram e em que posição (manchete, topo, secundario, editoria). Agrupe matérias diferentes sobre o MESMO fato em uma notícia só.
   Portais com "fetch": false no config (ex.: G1) bloqueiam leitura automática: NÃO tente abri-los nem contorne o bloqueio (curl, cache, espelho, outro domínio). Inclua-os em outlets só quando uma matéria deles aparecer nos resultados do WebSearch ou for citada por outro portal (position "editoria" se não souber o destaque). A ausência deles não reduz a nota de cobertura e não conta para a assimetria. Se outro portal falhar, siga com os demais.
3. Para cada notícia, dê nota de 0 a 10 a cada critério exatamente como descrito em config.criteria[].how e calcule score = soma(nota x weight) / soma(weights), com uma casa decimal. Descarte notícias com menos de config.min_outlets portais.
4. Distribua as notícias nos blocos de config.segments (cada notícia em um só bloco) e mantenha no máximo config.items_per_segment por bloco, as de maior nota. Escolha as config.top_overall de maior nota entre todos os blocos para "top".
5. Para cada notícia: id (curto e estável; se for a mesma notícia da edição anterior, reutilize o id), segment, title (factual, sem opinião), summary com exatamente config.summary_lines frases em texto corrido (o fato, os números, o que está em jogo, a reação dos lados, o próximo passo), scores {cobertura, destaque, persistencia, peso_voce}, score, outlets [{name, url da matéria específica, camp do config, position}], asymmetry {flag, note} conforme config.asymmetry.rule (com flag true, a note diz quem deu com destaque e quem omitiu).
   Regras: só fatos confirmados nas matérias que você abriu; não invente números; resumo informativo, sem adjetivos de opinião; português do Brasil; NUNCA use travessão (— ou –).
6. Grave noticias/AAAA-MM-DD-HHh.json com {generated (ISO -03:00), headline (1 frase), top [ids], items [...]} e acrescente o id em noticias/index.json ("editions", ordenada, sem duplicar, só as 90 mais recentes). Valide o JSON com python3 e confirme com grep que não há "—" nem "–".
7. Rode git status e desfaça qualquer alteração fora dos arquivos permitidos. git add só deles, commit "Notícias HHh de DD/MM" e push para main (git pull --rebase origin main antes; repita até 3 vezes se o push for recusado).

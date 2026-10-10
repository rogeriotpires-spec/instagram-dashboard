# Análise por período (muda com o filtro do painel)

Usado pela rotina do GitHub todo dia às 5h40 e pelo botão do painel. Você está na raiz do repositório rogeriotpires-spec/instagram-dashboard (branch main), já clonado.

Você escreve a análise de desempenho do Instagram @rogerrio (Rogerio Pires, jornalista e comentarista político no Rio de Janeiro) para cada período do filtro do painel: 1, 7, 30, 60 e 90 dias. Hora atual: sempre com `TZ=America/Sao_Paulo date -Iseconds`.

## Segurança do repositório
Só crie ou altere os arquivos analises/p1.json, analises/p7.json, analises/p30.json, analises/p60.json e analises/p90.json. Scripts auxiliares ficam em /tmp. Não faça commit nem push: o GitHub publica depois. Não pesquise na web: tudo vem dos dados.

## Passos
1. Rode `python3 tools/analise_dados.py > /tmp/dados.json` e leia /tmp/dados.json. Todos os números já estão calculados ali, por período: conta (views, compartilhamentos, comentários, salvamentos, novos seguidores, deixaram de seguir, alcance médio por dia, contas com interação por dia), variação percentual contra o período anterior (no período de 1 dia, contra a média dos 7 dias anteriores), publicações por formato, melhores e piores posts, posts que mais trouxeram seguidores e comentários, mediana de views por hora e por tema. Leia também analysis.json (a análise semanal, que é o modelo de tom e de formato).
2. Para cada período, escreva analises/p<N>.json neste formato:
   ```
   {"generated": "<agora ISO -03:00>", "dias": N, "period": {"start": "...", "end": "..."}, "compare": "<texto do campo comparado_com>",
    "headline": "<uma frase que resume o período com números>",
    "kpis": [6 itens {"label", "value", "delta", "dir"}],
    "cards": [4 itens {"title", "status", "status_label", "diagnosis", "actions", "posts"}]}
   ```
   - kpis: Views recebidas pela conta, Novos seguidores, Deixaram de seguir, Compartilhamentos recebidos, Comentários recebidos e um sexto à sua escolha (o que mais mudou). value no formato brasileiro (5,5 mi; 146 mil; 3.277); delta como "+31%" ou "−3%" (use o sinal de menos −, não hífen nem travessão); dir "up" quando a variação é boa para o perfil (menos gente deixando de seguir é "up"). Se a variação não existir (sem dado do período anterior), delta "sem comparação" e dir "na".
   - cards: "O que funcionou", "O que não funcionou" e mais dois títulos escolhidos pelo que mais mudou no período (comentários, seguidores, retenção dos Reels, horários, temas, formatos). status good, warn ou bad; status_label curto ("Repetir", "Ajustar", "Atenção"); diagnosis com 2 a 3 frases citando posts e números do arquivo; actions com 2 a 4 ações concretas; posts com os códigos citados.
   - Período de 1 dia: foque no que aconteceu naquele dia (posts publicados, picos, o que puxou views e seguidores) e compare com a média da semana anterior. Se houver poucos posts, diga isso e use os cards para o que o dia ensina.
   - Períodos longos (60 e 90 dias): foque em padrões (formatos, horários, temas, estilos) e tendências, não em posts isolados.
   - Atenção: posts recentes ainda acumulam views; não compare a mediana de posts de idades muito diferentes sem dizer isso.
3. Regras de texto: português do Brasil, direto e curto, sem jargão; NUNCA use travessão (— ou –); trate o leitor como "você"; todo número citado precisa estar em /tmp/dados.json; não atribua causalidade ("esteve associado", "coincidiu").
4. Valide: `python3 -c "import json;[json.load(open(f'analises/p{n}.json')) for n in (1,7,30,60,90)]"`, confira que cada arquivo tem 6 kpis e 4 cards e que não há "—" nem "–" (grep).

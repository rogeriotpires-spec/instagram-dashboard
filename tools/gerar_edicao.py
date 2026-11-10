"""Publica uma edição básica, sem IA, exclusivamente de manchetes coletadas.
Uso: python3 tools/gerar_edicao.py coleta-noticias.json
Não cria fatos nem afirmações além dos títulos das fontes.
"""
import json
import re
import sys
import hashlib
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'noticias/config.json'
INDEX = ROOT / 'noticias/index.json'
STOP = set('de da do das dos e em no na nos nas ao aos para por com sem que um uma uns umas os as o a sobre entre após depois antes mais menos como ainda hoje ontem amanhã diz disse diz que segundo aponta veja entenda saiba revela brasil brasileira brasileiro governo país novo nova contra este esta pelo pela pelos pelas sob durante'.split())
KEYWORDS = {
    'educacao': ('enem', 'escola', 'educa', 'aluno', 'professor', 'universidad', 'vestibular', 'inep', 'enade', 'fies', 'mec ', 'faetec'),
    'rio': ('rio de janeiro', 'fluminense', 'baixada', 'niterói', 'alerj', 'caxias', 'prefeitura do rio', 'governo do rio', 'maricá'),
    'ciencia': ('inteligência artificial', 'tecnologia', 'ciência', 'cientista', 'pesquisa', 'startup', 'nvidia', 'openai', 'chatgpt', 'nasa', 'satélite', 'foguete'),
    'esportes': ('flamengo', 'vasco', 'fluminense', 'botafogo', 'palmeiras', 'corinthians', 'futebol', 'copa do brasil', 'libertadores', 'seleção brasileira', 'brasileirão'),
    'seguranca': ('polícia', 'policial', 'crime', 'prisão', 'preso', 'tráfico', 'facção', 'homicídio', 'assassinato', 'feminicídio', 'operação policial'),
    'economia': ('economia', 'inflação', 'selic', 'juros', 'dólar', 'bolsa', 'pib', 'imposto', 'receita federal', 'emprego', 'desemprego', 'salário', 'mercado financeiro', 'banco central'),
    'internacional': ('trump', 'putin', 'ucrânia', 'rússia', 'estados unidos', 'eua', 'china', 'israel', 'iran', 'irã', 'venezuela', 'argentina', 'onu'),
}


def norm(s):
    return ''.join(x for x in unicodedata.normalize('NFKD', s.lower()) if not unicodedata.combining(x))


def tokens(s):
    return {w for w in re.findall(r'[a-z0-9]{3,}', norm(s)) if w not in STOP}


def segment(title, hint=''):
    if hint in ('educacao', 'rio', 'ciencia', 'esportes'):
        return hint
    t = norm(title)
    # Preferir o lugar quando se trata de política/administração local, e não de futebol.
    for seg in ('educacao', 'esportes', 'ciencia', 'rio', 'seguranca', 'economia', 'internacional'):
        if any(norm(k) in t for k in KEYWORDS[seg]):
            return seg
    return 'politica'


def coverage(n):
    if n >= 9: return 10
    if n >= 6: return round(8 + (n - 6) * 2 / 3, 1)
    if n >= 3: return round(5 + (n - 3), 1)
    if n >= 1: return round(2 + (n - 1) * 1.5, 1)
    return 0


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else 'coleta-noticias.json')
    data = json.loads(src.read_text(encoding='utf-8'))
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    tz = timezone(timedelta(hours=-3))
    now = datetime.now(tz)
    edition = now.strftime('%Y-%m-%d') + '-' + ('06h' if now.hour < 9 else '12h' if now.hour < 16 else '19h')
    entries = []
    for p in data.get('portais', []):
        for m in p.get('manchetes', []):
            if m.get('titulo') and m.get('link'):
                entries.append(dict(title=m['titulo'].strip(), name=p['nome'], camp=p.get('camp', ''), url=m['link'], hint='', when=m.get('quando', '')))
    for key, matches in data.get('temas', {}).items():
        for m in matches:
            if m.get('titulo') and m.get('link'):
                entries.append(dict(title=m['titulo'].strip(), name=m.get('fonte', 'Fonte'), camp='', url=m['link'], hint=key, when=m.get('quando', '')))
    if len(entries) < 25:
        raise SystemExit('Coleta insuficiente: não publicar edição vazia ou incompleta')
    groups = []
    for entry in entries:
        ts = tokens(entry['title'])
        if len(ts) < 2: continue
        best, similarity = None, 0
        for g in groups:
            if entry['hint'] and g['hint'] and entry['hint'] != g['hint']: continue
            common = len(ts & g['terms'])
            ratio = common / max(1, min(len(ts), len(g['terms'])))
            if common >= 3 and ratio >= .68 and ratio > similarity:
                best, similarity = g, ratio
        if best is None:
            groups.append(dict(items=[entry], terms=ts, hint=entry['hint']))
        else:
            best['items'].append(entry)
            # Conservar conjunto do primeiro título para não fundir temas por transitividade.
    index = json.loads(INDEX.read_text(encoding='utf-8'))
    previous = set()
    for prev in index.get('editions', [])[-12:]:
        if prev[:10] == edition[:10]: continue
        f = ROOT / 'noticias' / (prev + '.json')
        if not f.exists(): continue
        try: previous.update(tokens(item.get('title', '')) and tuple(sorted(tokens(item.get('title', '')))) for item in json.loads(f.read_text(encoding='utf-8')).get('items', []))
        except (ValueError, OSError): pass
    weights = {c['key']: c['weight'] for c in cfg['criteria']}
    byseg = defaultdict(list)
    for g in groups:
        items = list({e['name']: e for e in g['items']}.values())
        items.sort(key=lambda e: (e['hint'] != '', len(e['title'])))
        lead = items[0]
        title = lead['title'].rstrip('. ')
        seg = segment(title, lead['hint'])
        sconf = next((s for s in cfg['segments'] if s['key'] == seg), {})
        if len(items) < sconf.get('min_outlets', cfg['min_outlets']): continue
        if tuple(sorted(tokens(title))) in previous: continue
        outlets = [{'name': x['name'], 'url': x['url'], 'camp': x['camp'], 'position': 'editoria'} for x in items]
        scores = {'cobertura': coverage(len(items)), 'destaque': 3, 'persistencia': 4, 'peso_voce': 10 if seg in ('politica','seguranca','rio','educacao') else 6}
        total = round(sum(scores[k]*weights[k] for k in weights)/sum(weights.values()), 1)
        ident = hashlib.sha256(norm(title).encode()).hexdigest()[:12]
        # Uma frase factual apenas, derivada do título; não fabricar o contexto.
        summary = f"{title}. Manchete coletada automaticamente; consulte as fontes indicadas para confirmar os detalhes e os desdobramentos."
        byseg[seg].append({'id': ident, 'segment': seg, 'title': title, 'summary': summary, 'scores': scores, 'score': total, 'outlets': outlets, 'asymmetry': {'flag': False, 'note': ''}})
    selected = []
    for s in cfg['segments']:
        selected.extend(sorted(byseg[s['key']], key=lambda x: (-x['score'], -len(x['outlets'])))[:cfg['items_per_segment']])
    if len(selected) < 8:
        raise SystemExit('Poucos assuntos com cobertura suficiente: edição não publicada')
    selected.sort(key=lambda it: (-it['score'], cfg['segments'].index(next(s for s in cfg['segments'] if s['key']==it['segment']))))
    top = selected[:cfg['top_overall']]
    headline = '; '.join(i['title'] for i in top[:3])
    result = {'generated': now.replace(microsecond=0).isoformat(), 'headline': headline, 'top': [x['id'] for x in top], 'items': selected, 'x_trends': []}
    dest = ROOT / 'noticias' / (edition + '.json')
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    editions = sorted(set(index.get('editions', []) + [edition]))[-90:]
    INDEX.write_text(json.dumps({'editions': editions}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Gerada {dest.name}: {len(selected)} assuntos; {len(entries)} manchetes analisadas')


if __name__ == '__main__': main()

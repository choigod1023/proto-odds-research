import csv,collections,html
from pathlib import Path
R=Path(__file__).resolve().parent
rows=list(csv.DictReader((R/'calibration.csv').open(encoding='utf8'))); colors=['#0466c8','#d1495b','#2a9d8f','#8338ec','#e09f3e','#59656f']
parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="900" viewBox="0 0 1200 900"><rect width="100%" height="100%" fill="white"/><text x="25" y="30" font-family="sans-serif" font-size="22">Retrospective test: Shin calibration (largest groups)</text>']
for idx,dimension in enumerate(['league','odds','side','way']):
 x0=70+(idx%2)*580;y0=80+(idx//2)*400;w=400;h=300
 groups=collections.defaultdict(list)
 for r in rows:
  if r['dimension']==dimension:groups[r['group']].append(r)
 leaders=sorted(groups,key=lambda g:sum(int(r['n']) for r in groups[g]),reverse=True)[:6]
 parts.append(f'<g font-family="sans-serif" font-size="14"><text x="{x0}" y="{y0-15}">{dimension}</text><path d="M {x0} {y0} V {y0+h} H {x0+w}" fill="none" stroke="black"/><path d="M {x0} {y0+h} L {x0+w} {y0}" fill="none" stroke="#999" stroke-dasharray="4"/><text x="{x0}" y="{y0+h+22}">0</text><text x="{x0+w-10}" y="{y0+h+22}">1</text><text x="{x0+100}" y="{y0+h+40}">predicted probability</text><text x="{x0-45}" y="{y0+10}">1</text><text x="{x0-45}" y="{y0+h}">0</text><text x="{x0-62}" y="{y0+h/2}" transform="rotate(-90 {x0-62} {y0+h/2})">observed frequency</text></g>')
 for i,group in enumerate(leaders):
  sample=sorted(groups[group],key=lambda r:int(r['probability_bin']));points=' '.join(f"{x0+float(r['predicted'])*w:.2f},{y0+h-float(r['observed'])*h:.2f}" for r in sample)
  parts.append(f'<polyline points="{points}" fill="none" stroke="{colors[i]}" stroke-width="2"/><text x="{x0+w+10}" y="{y0+18+i*22}" font-family="sans-serif" font-size="12" fill="{colors[i]}">{html.escape(group)}</text>')
parts.append('</svg>');(R/'calibration.svg').write_text(''.join(parts)+'\n',encoding='utf8',newline='\n')

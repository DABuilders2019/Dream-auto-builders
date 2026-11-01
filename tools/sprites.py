"""Pack the ReadyLift photos into sprite sheets (the hosted prototype allows max 512 files).
Rewrites readylift.js so each image is 'rl:<sheet>:<tile>'."""
import json, os, re, sys
from PIL import Image
root = sys.argv[1]; T = 400; C = 8; R = 8
src = open(os.path.join(root, 'readylift.js')).read()
m = re.search(r'window\.DAB_RL = (.*?);\nwindow\.DAB_RL_VEH = (.*?);\n', src, re.S)
P = json.loads(m.group(1)); V = m.group(2)
P_sorted = sorted(P, key=lambda p: -p['popularity'])
order = []
for p in P_sorted:
    for u in p['imgs']:
        if u not in order: order.append(u)
os.makedirs(os.path.join(root, 'img', 'rls'), exist_ok=True)
ref = {}
for s in range(0, len(order), C * R):
    sheet = Image.new('RGB', (C * T, R * T), 'white')
    for i, u in enumerate(order[s:s + C * R]):
        im = Image.open(os.path.join(root, u)).convert('RGB'); im.thumbnail((T - 8, T - 8))
        sheet.paste(im, ((i % C) * T + (T - im.width) // 2, (i // C) * T + (T - im.height) // 2))
        ref[u] = f'rl:{s // (C * R)}:{i}'
    sheet.save(os.path.join(root, 'img', 'rls', f'{s // (C * R)}.jpg'), quality=78, optimize=True, progressive=True)
for p in P:
    p['imgs'] = [ref[u] for u in p['imgs']]; p['img'] = p['imgs'][0] if p['imgs'] else None
out = src[:m.start()] + 'window.DAB_RL = ' + json.dumps(P, separators=(',', ':')) + ';\nwindow.DAB_RL_VEH = ' + V + ';\nwindow.DAB_SPRITE = {cols:%d,rows:%d};\n' % (C, R) + src[m.end():]
open(os.path.join(root, 'readylift.js'), 'w').write(out)
print('images', len(order), 'sheets', (len(order) + C * R - 1) // (C * R))

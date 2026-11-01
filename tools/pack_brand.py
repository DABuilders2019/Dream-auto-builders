"""Pack a brand's photos into sprite sheets and write <brand>.js for the prototype."""
import json, os, sys, hashlib
from PIL import Image
src_json, dl_dir, site, pfx, var = sys.argv[1:6]
T, C, R = 300, 8, 8
data = json.load(open(src_json)); P = data['products']
md5 = {}; order = []
for p in sorted(P, key=lambda p: -p['popularity']):
    f = os.path.join(dl_dir, p['id'] + '.jpg')
    if not os.path.exists(f): continue
    h = hashlib.md5(open(f, 'rb').read()).hexdigest()
    if h not in md5: md5[h] = len(order); order.append(f)
    p['_h'] = h
os.makedirs(os.path.join(site, 'img', pfx + 's'), exist_ok=True)
for s in range(0, len(order), C * R):
    sheet = Image.new('RGB', (C * T, R * T), 'white')
    for i, f in enumerate(order[s:s + C * R]):
        im = Image.open(f).convert('RGB'); im.thumbnail((T - 8, T - 8))
        sheet.paste(im, ((i % C) * T + (T - im.width) // 2, (i // C) * T + (T - im.height) // 2))
    sheet.save(os.path.join(site, 'img', pfx + 's', f'{s // (C * R)}.jpg'), quality=76, optimize=True, progressive=True)
for p in P:
    h = p.pop('_h', None)
    if h is not None:
        k = md5[h]; ref = f'{pfx}:{k // (C * R)}:{k % (C * R)}'; p['imgs'] = [ref]; p['img'] = ref
    p.pop('photoUrl', None)
with open(os.path.join(site, var.lower().replace('dab_', '') + '.js'), 'w') as f:
    f.write('/* Generated from SEMA Data (PIES 7.0 + Admark vehicle sheet). Do not edit by hand. */\n')
    f.write(f'window.{var} = ' + json.dumps(P, separators=(',', ':')) + ';\n')
    f.write(f'window.{var}_VEH = ' + json.dumps(data['vehicles'], separators=(',', ':')) + ';\n')
print('photos', len(order), 'sheets', (len(order) + 63) // 64, 'with img', sum(1 for p in P if p['img']))

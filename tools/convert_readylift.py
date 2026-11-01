"""Convert SEMA Data PIES 7.0 + ACES 4.0 XML into the storefront's product format.
usage: convert.py <pies.xml> <aces.xml> <image_dir> <out_dir> [vehicle_names.xlsx]
The optional xlsx is SEMA's "Admark Vehicle Assignment Template" export: the same ACES fitment with vehicle names."""
import sys, os, json, re, collections, xml.etree.ElementTree as ET
from PIL import Image
pies_f, aces_f, img_dir, out = sys.argv[1:5]
names_f = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5].endswith('.xlsx') else None
review_f = next((a for a in sys.argv[5:] if a.endswith('.json')), None)
REVIEW = json.load(open(review_f)) if review_f else {'bad': {}, 'veh': []}
BAD, VEHSHOT = REVIEW['bad'], set(REVIEW['veh'])
DL = collections.defaultdict(list)   # photos downloaded from the PIES links: pn -> [(k, md5, url)]
if os.path.exists(os.path.join(os.path.dirname(review_f or ''), 'dl_results.json')):
    for r in json.load(open(os.path.join(os.path.dirname(review_f), 'dl_results.json'))):
        if r['ok']: DL[r['pn']].append((r['k'], r['md5'], r['url']))
DLBAD, DLVEH = set(REVIEW.get('dl_bad_md5', [])), set(REVIEW.get('dl_veh_md5', []))
import shutil
report = []
NS = {'p': 'http://www.autocare.org'}
os.makedirs(os.path.join(out, 'img', 'rl'), exist_ok=True)

SUBMAP = {'Suspension Lift Kit': 'lift-kits', 'Suspension Leveling Kit': 'leveling-kits',
          'Suspension Leaf Spring Block Kit': 'lift-blocks', 'Suspension Shock Absorber': 'shocks',
          'Suspension Shock Absorber Kit': 'shocks', 'Wheel Spacer': 'wheel-spacers'}
CATOF = {'wheel-spacers': 'wheels'}

# ---- ACES: group applications by part
apps = collections.defaultdict(list)
for a in ET.parse(aces_f).getroot().findall('App'):
    y = a.find('Years')
    row = {'from': int(y.get('from')), 'to': int(y.get('to')),
           'make': a.find('Make').get('id'), 'model': a.find('Model').get('id')}
    for k in ('SubModel', 'DriveType', 'FuelType'):
        e = a.find(k)
        if e is not None: row[k[0].lower() + k[1:]] = e.get('id')
    notes = [n.text.strip().strip('"') for n in a.findall('Note') if n.text]
    notes += [q.findtext('text') for q in a.findall('Qual') if q.findtext('text')]
    if notes: row['notes'] = '; '.join(notes)
    apps[a.findtext('Part')].append(row)

# ---- Named fitment (Year/Make/Model/Submodel as words) from the Admark export
named = collections.defaultdict(lambda: collections.defaultdict(lambda: {'years': set(), 'subs': set()}))
vehicles = collections.defaultdict(set)
if names_f:
    import openpyxl
    ws = openpyxl.load_workbook(names_f, read_only=True).worksheets[0]
    rows = ws.iter_rows(values_only=True); hdr = next(rows)
    for r in rows:
        make, model, year, sub, sku, note, label = r[0], r[1], int(r[2]), r[3] or '', r[7], r[9] or '', r[25] or ''
        g = named[sku][(make, model, ' '.join(note.strip().strip('"').split()))]
        g['years'].add(year)
        if sub: g['subs'].add(sub)
        vehicles[(make, model)].add(year)

def ranges(years):
    ys = sorted(years); out = []; start = prev = ys[0]
    for y in ys[1:]:
        if y != prev + 1: out.append((start, prev)); start = y
        prev = y
    out.append((start, prev)); return out

def named_rows(pn):
    rows = []
    for (make, model, note), g in sorted(named.get(pn, {}).items()):
        for a, b in ranges(g['years']):
            rows.append({'from': a, 'to': b, 'make': make, 'model': model, 'sub': ', '.join(sorted(g['subs'])) or 'All', 'engine': '', 'notes': note})
    rows.sort(key=lambda r: (r['make'], r['model'], r['from']))
    return rows

z = set(os.listdir(img_dir)); done = {}
def save_img(fn):
    if fn in done: return done[fn]
    src = os.path.join(img_dir, fn)
    try:
        im = Image.open(src)
        if im.mode in ('RGBA', 'LA', 'P'):
            im = im.convert('RGBA'); bg = Image.new('RGB', im.size, (255, 255, 255)); bg.paste(im, mask=im.split()[-1]); im = bg
        else: im = im.convert('RGB')
        im.thumbnail((800, 800))
        name = re.sub(r'[^a-z0-9]+', '-', os.path.splitext(fn)[0].lower()).strip('-') + '.jpg'
        im.save(os.path.join(out, 'img', 'rl', name), quality=80, optimize=True)
        done[fn] = 'img/rl/' + name
    except Exception as e:
        print('skip image', fn, e); done[fn] = None
    return done[fn]

products = []
for it in ET.parse(pies_f).getroot().findall('.//p:Items/p:Item', NS):
    t = lambda path: (it.findtext(path, namespaces=NS) or '').strip()
    pn = t('p:PartNumber')
    desc = collections.defaultdict(list)
    for d in it.findall('.//p:Description', NS):
        if d.text: desc[d.get('DescriptionCode')].append(' '.join(d.text.split()))
    name = (desc['EXT'] or desc['INV'] or [pn])[0]
    ptype = (desc['DEF'] or ['Suspension Component'])[0]
    sub = SUBMAP.get(ptype, 'suspension-components')
    feats = []
    for f in desc['FAB']:
        if f != name and f not in feats: feats.append(f)
    price = None
    for pr in it.findall('.//p:Pricing', NS):
        v = pr.findtext('p:Price', namespaces=NS)
        if v: price = round(float(v), 2)
    attrs = {}; prop65 = ''
    for a in it.findall('.//p:ProductAttribute', NS):
        aid, val = a.get('AttributeID'), ' '.join((a.text or '').split())
        if aid == 'California Proposition 65': prop65 = val
        elif a.get('PADBAttribute') == 'N' and not aid.startswith('Prop65') and val and aid not in attrs: attrs[aid] = val
    ext = {e.get('EXPICode'): (e.text or '').strip() for e in it.findall('.//p:ExtendedProductInformation', NS)}
    pk = it.find('.//p:Package', NS); weight = None; dims = ''
    if pk is not None:
        w = pk.findtext('.//p:Weight', namespaces=NS); weight = round(float(w), 1) if w else None
        dm = pk.find('p:Dimensions', NS)
        if dm is not None:
            dims = ' x '.join(str(round(float(dm.findtext('p:' + k, namespaces=NS) or 0), 1)) for k in ('Length', 'Width', 'Height')) + ' in.'
    imgs, pdfs, cands, photo_urls, bad_found, missing = [], [], [], [], [], []
    for da in sorted(it.findall('.//p:DigitalFileInformation', NS), key=lambda d: {'P04': 0, 'P01': 1}.get(d.findtext('p:AssetType', namespaces=NS), 2)):
        fn, at, uri = (da.findtext('p:' + k, namespaces=NS) or '' for k in ('FileName', 'AssetType', 'URI'))
        if at == 'INS':
            if uri: pdfs.append(uri)
            continue
        if uri: photo_urls.append(uri)
        if fn not in z: missing.append(fn); continue
        p = save_img(fn)
        if not p: continue
        base = os.path.basename(p)
        if base in BAD: bad_found.append((fn, BAD[base])); continue
        if p not in cands: cands.append(p)
    from_links = 0; from_site = False
    if not any(os.path.basename(p) not in VEHSHOT for p in cands):     # no part photo yet: use the downloaded link photos
        seen = set()
        for k, md5, url in sorted(DL.get(pn, [])):
            if md5 in DLBAD or md5 in seen: continue
            seen.add(md5)
            fname = re.sub(r'[^a-z0-9]+', '-', pn.lower()) + f'-l{k}.jpg'
            shutil.copy(os.path.join(os.path.dirname(review_f), 'dl', pn, f'{k}.jpg'), os.path.join(out, 'img', 'rl', fname))
            if md5 in DLVEH: VEHSHOT.add(fname)
            cands.append('img/rl/' + fname); from_links += 1
            if k >= 100: from_site = True
    cands.sort(key=lambda p: os.path.basename(p) in VEHSHOT)   # part photos first, vehicle shots after
    imgs = cands[:4]
    specs = {'Part Number': pn, 'Brand': 'ReadyLift', 'Part Type': ptype}
    specs.update(attrs)
    if ext.get('CTO'): specs['Country of Origin'] = ext['CTO']
    gt = t('p:ItemLevelGTIN')
    if gt: specs['UPC'] = gt.lstrip('0') or gt
    if weight: specs['Shipping Weight'] = f'{weight} lb'
    if dims: specs['Package Dimensions'] = dims
    rows = apps.get(pn, [])
    fit = named_rows(pn)
    n_good = len(cands); veh_only = n_good > 0 and all(os.path.basename(p) in VEHSHOT for p in cands)
    if from_site: status = 'Fixed: photo added from ReadyLift.com product page'
    elif from_links and not veh_only: status = 'Fixed: photo added from SEMA link'
    elif from_links: status = 'Fixed: vehicle photo added from SEMA link'
    elif n_good == 0 and bad_found: status = 'Only unusable photos'
    elif n_good == 0: status = 'No photo'
    elif veh_only: status = 'Vehicle photo only (no part photo)'
    elif bad_found: status = 'OK after removing unusable photo'
    else: status = 'OK'
    report.append({'pn': pn, 'name': name, 'ptype': ptype, 'price': price, 'status': status, 'on_site': len(imgs),
                   'removed': '; '.join(f'{f} ({why})' for f, why in bad_found), 'missing_files': len(missing),
                   'photo_urls': len(photo_urls), 'first_url': photo_urls[0] if photo_urls else '', 'online': price is not None})
    products.append({
        'id': 'rl-' + re.sub(r'[^a-z0-9]+', '-', pn.lower()), 'pn': pn, 'brand': 'readylift', 'brandName': 'ReadyLift',
        'cat': CATOF.get(sub, 'suspension'), 'sub': sub, 'ptype': ptype,
        'title': 'ReadyLift ' + name, 'shortName': name, 'price': price, 'msrp': None,
        'weight': weight, 'features': feats[:10], 'marketing': (desc['MKT'] or [''])[0],
        'specs': specs, 'prop65': prop65, 'pdfs': pdfs[:2], 'imgs': imgs, 'img': imgs[0] if imgs else None,
        'fitPending': bool(rows) and not fit, 'universal': False, 'fitCount': len(rows),
        'years': [min(r['from'] for r in rows), max(r['to'] for r in rows)] if rows else None,
        'fitRows': rows[:60], 'fitment': fit, 'stock': None, 'real': True,
        'popularity': 500 + (len(imgs) * 100),
        'kw': ' '.join([pn, name, ptype, 'readylift', ' '.join(feats[:4])]).lower()
    })

json.dump(report, open(os.path.join(os.path.dirname(os.path.abspath(review_f or out)), 'photo_report.json'), 'w'), indent=1)
print(collections.Counter(r['status'] for r in report))
with open(os.path.join(out, 'readylift.js'), 'w') as f:
    f.write('/* Generated from SEMA Data: ReadyLift PIES 7.0 + ACES 4.0 (2026-10-19). Do not edit by hand. */\n')
    f.write('window.DAB_RL = ' + json.dumps(products, separators=(',', ':')) + ';\n')
    f.write('window.DAB_RL_VEH = ' + json.dumps([{'make': k[0], 'model': k[1], 'years': sorted(v)} for k, v in sorted(vehicles.items())], separators=(',', ':')) + ';\n')
c = collections.Counter(p['sub'] for p in products)
print('products', len(products), 'priced', sum(1 for p in products if p['price']), 'with images', sum(1 for p in products if p['img']),
      'images written', sum(1 for v in done.values() if v), 'fit rows', sum(len(v) for v in apps.values()),
      'named fitment rows', sum(len(p['fitment']) for p in products), 'parts with names', sum(1 for p in products if p['fitment']), 'vehicle lines', len(vehicles))
print(c)

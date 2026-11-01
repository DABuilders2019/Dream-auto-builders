"""Convert one brand's SEMA Data exports (PIES 7.0 XML + Admark Vehicle Assignment xlsx)
into the storefront's product format.

usage: convert_brand.py <pies.xml> <admark.xlsx> <out_dir> <brand_id> <brand_name> <id_prefix> <price_type> [sample.json]
  price_type: RET (retail), RMP (MAP), LST (list) ...
  sample.json: optional list of part numbers to include (prototype size limit)

Writes <out_dir>/<brand_id>.json with products, vehicles and the photo URL for each part.
"""
import sys, os, re, json, collections, xml.etree.ElementTree as ET
import openpyxl

pies_f, names_f, out, BID, BNAME, PFX, PRICE = sys.argv[1:8]
sample = set(json.load(open(sys.argv[8]))) if len(sys.argv) > 8 else None
NS = {'p': 'http://www.autocare.org'}

# Part type (PIES "DEF" description) -> storefront category / subcategory
def place(ptype, text):
    t = text.lower()
    if 'catalytic' in ptype.lower() or 'oxidation catalyst' in ptype.lower(): return 'exhaust', 'catalytic-converters'
    if ptype in ('Exhaust System Kit', 'Exhaust Muffler Kit'):
        if 'axle-back' in t or 'axle back' in t: return 'exhaust', 'axle-back'
        if 'cat-back' in t or 'cat back' in t: return 'exhaust', 'cat-back'
        return 'exhaust', 'exhaust-kits'
    if ptype == 'Exhaust Muffler': return 'exhaust', 'mufflers'
    if ptype == 'Exhaust Tail Pipe Tip': return 'exhaust', 'exhaust-tips'
    if 'header' in ptype.lower(): return 'exhaust', 'headers'
    return 'exhaust', 'exhaust-pipes'

# ---- vehicle names (Admark sheet): SKU -> grouped year ranges
named = collections.defaultdict(lambda: collections.defaultdict(lambda: {'years': set(), 'subs': set(), 'eng': set()}))
vehicles = collections.defaultdict(set)
ws = openpyxl.load_workbook(names_f, read_only=True).worksheets[0]
rows = ws.iter_rows(values_only=True); next(rows)
for r in rows:
    sku = str(r[7]).strip()
    if sample and sku not in sample: continue
    make, model, year = r[0], r[1], int(r[2])
    note = ' '.join(str(r[9] or '').strip().strip('"').split())
    g = named[sku][(make, model, note)]
    g['years'].add(year)
    if r[3]: g['subs'].add(str(r[3]))
    if r[12]: g['eng'].add(str(r[12]))
    vehicles[(make, model)].add(year)

def ranges(ys):
    ys = sorted(ys); out_, a = [], ys[0]; p = a
    for y in ys[1:]:
        if y != p + 1: out_.append((a, p)); a = y
        p = y
    out_.append((a, p)); return out_

def fit_rows(pn):
    res = []
    for (make, model, note), g in sorted(named.get(pn, {}).items()):
        for a, b in ranges(g['years']):
            res.append({'from': a, 'to': b, 'make': make, 'model': model,
                        'sub': ', '.join(sorted(g['subs'])) or 'All', 'engine': ', '.join(sorted(g['eng']))[:80], 'notes': note})
    return sorted(res, key=lambda r: (r['make'], r['model'], r['from']))

products = []
for it in ET.parse(pies_f).getroot().findall('.//p:Items/p:Item', NS):
    t = lambda path: (it.findtext(path, namespaces=NS) or '').strip()
    pn = t('p:PartNumber')
    if sample and pn not in sample: continue
    desc = collections.defaultdict(list)
    for d in it.findall('.//p:Description', NS):
        if d.text: desc[d.get('DescriptionCode')].append(' '.join(d.text.split()))
    attrs = {}
    for a in it.findall('.//p:ProductAttribute', NS):
        if a.get('PADBAttribute') == 'N' and a.text and a.text.strip(): attrs.setdefault(a.get('AttributeID'), ' '.join(a.text.split()))
    name = (attrs.get('Title') or (desc['EXT'] or desc['DES'] or desc['INV'] or [pn])[0])
    name = re.sub(r'^MagnaFlow\s+', '', name, flags=re.I) if BID == 'magnaflow' else name
    ptype = (desc['DEF'] or ['Exhaust Part'])[0]
    cat, sub = place(ptype, ' '.join([name] + desc['SHO'] + desc['DES']))
    price = None
    for pr in it.findall('.//p:Pricing', NS):
        if pr.get('PriceType') == PRICE:
            v = pr.findtext('p:Price', namespaces=NS); price = round(float(v), 2) if v else None
    feats = [f for f in dict.fromkeys(desc['FAB']) if f != name][:10]
    eo = attrs.get('CARB Executive Order Number', '')
    emissions = None
    if sub == 'catalytic-converters':
        emissions = ('carb', eo) if eo and eo != 'Not Applicable' else ('federal', '')
    prop65 = attrs.get('California Proposition 65') or ('' if attrs.get('Prop 65 Y/N', 'No') == 'No' else 'WARNING: This product can expose you to chemicals known to the State of California to cause cancer and/or reproductive harm. www.P65Warnings.ca.gov')
    specs = {'Part Number': pn, 'Brand': BNAME, 'Part Type': ptype}
    for k in ('Series', 'Finish', 'Material', 'Tip Diameter', 'Inlet Diameter', 'Outlet Diameter'):
        if attrs.get(k): specs[k] = attrs[k]
    if emissions: specs['Emissions'] = f'California legal, CARB EO {eo}' if emissions[0] == 'carb' else 'Federal (49-state). Not legal for sale in California or CARB states.'
    ext = {e.get('EXPICode'): (e.text or '').strip() for e in it.findall('.//p:ExtendedProductInformation', NS)}
    if ext.get('CTO'): specs['Country of Origin'] = ext['CTO']
    gt = t('p:ItemLevelGTIN')
    if gt: specs['UPC'] = gt.lstrip('0')
    pk = it.find('.//p:Package', NS); weight = None
    if pk is not None:
        w = pk.findtext('.//p:Weight', namespaces=NS); weight = round(float(w), 1) if w else None
        dm = pk.find('p:Dimensions', NS)
        if dm is not None:
            specs['Package Dimensions'] = ' x '.join(str(round(float(dm.findtext('p:' + k, namespaces=NS) or 0), 1)) for k in ('Length', 'Width', 'Height')) + ' in.'
    if weight: specs['Shipping Weight'] = f'{weight} lb'
    photo, pdfs = None, []
    for da in sorted(it.findall('.//p:DigitalFileInformation', NS), key=lambda d: {'P04': 0, 'P01': 1}.get(d.findtext('p:AssetType', namespaces=NS), 9)):
        at, uri, ft = (da.findtext('p:' + k, namespaces=NS) or '' for k in ('AssetType', 'URI', 'FileType'))
        if at in ('P04', 'P01') and ft in ('JPG', 'PNG') and uri and not photo: photo = uri
        if at == 'INS' and uri: pdfs.append(uri)
    fit = fit_rows(pn)
    products.append({
        'id': PFX + '-' + re.sub(r'[^a-z0-9]+', '-', pn.lower()), 'pn': pn, 'brand': BID, 'brandName': BNAME,
        'cat': cat, 'sub': sub, 'ptype': ptype, 'title': BNAME + ' ' + name, 'shortName': name,
        'price': price, 'msrp': None, 'weight': weight, 'features': feats, 'marketing': (desc['MKT'] or [''])[0][:900],
        'specs': specs, 'prop65': prop65, 'pdfs': pdfs[:2], 'emissions': emissions,
        'photoUrl': photo, 'imgs': [], 'img': None,
        'fitment': fit, 'fitPending': False, 'universal': not fit, 'fitText': None if fit else 'Universal fit',
        'stock': None, 'real': True, 'popularity': 300 + min(len(fit), 50),
        'kw': ' '.join([pn, name, ptype, BNAME.lower(), attrs.get('Series', ''), ' '.join(desc['KSW'])]).lower()
    })

json.dump({'products': products, 'vehicles': [{'make': k[0], 'model': k[1], 'years': sorted(v)} for k, v in sorted(vehicles.items())]},
          open(os.path.join(out, BID + '.json'), 'w'), separators=(',', ':'))
print('products', len(products), 'priced', sum(1 for p in products if p['price']), 'with photo url', sum(1 for p in products if p['photoUrl']),
      'with fitment', sum(1 for p in products if p['fitment']), 'vehicle lines', len(vehicles))
print(collections.Counter(p['sub'] for p in products))

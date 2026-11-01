import json, sys, os, subprocess, hashlib, concurrent.futures as cf
from PIL import Image
data=json.load(open(sys.argv[1])); d=sys.argv[2]
def get(p):
    if not p['photoUrl']: return p['pn'], None
    raw=f"{d}/{p['id']}.raw"; out=f"{d}/{p['id']}.jpg"
    if os.path.exists(out): return p['pn'], hashlib.md5(open(out,'rb').read()).hexdigest()
    subprocess.run(['curl','-sSL','--max-time','60','-o',raw,p['photoUrl']],capture_output=True)
    try:
        im=Image.open(raw); im.load()
        if im.mode in ('RGBA','LA','P'):
            im=im.convert('RGBA'); bg=Image.new('RGB',im.size,(255,255,255)); bg.paste(im,mask=im.split()[-1]); im=bg
        else: im=im.convert('RGB')
        im.thumbnail((600,600)); im.save(out,quality=82); os.remove(raw)
        return p['pn'], hashlib.md5(open(out,'rb').read()).hexdigest()
    except Exception as e: return p['pn'], None
with cf.ThreadPoolExecutor(16) as ex: res=dict(ex.map(get,data['products']))
json.dump(res,open(f"{d}/../dl_md5.json",'w'))
import collections
print('ok',sum(1 for v in res.values() if v),'fail',sum(1 for v in res.values() if not v))
print(collections.Counter(v for v in res.values() if v).most_common(6))

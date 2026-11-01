import xml.etree.ElementTree as ET, glob, json, os, re, subprocess, hashlib, concurrent.futures as cf
from PIL import Image
NS={'p':'http://www.autocare.org'}
rep={r['pn']:r for r in json.load(open('/home/claude/sema/photo_report.json'))}
need={pn for pn,r in rep.items() if r['status'] not in ('OK','OK after removing unusable photo')}
jobs=[]
for it in ET.parse(glob.glob('/home/claude/sema/pies/*.xml')[0]).getroot().findall('.//p:Items/p:Item',NS):
    pn=it.findtext('p:PartNumber',namespaces=NS)
    if pn not in need: continue
    for k,da in enumerate(sorted(it.findall('.//p:DigitalFileInformation',NS), key=lambda d:{'P04':0,'P01':1}.get(d.findtext('p:AssetType',namespaces=NS),2))):
        if da.findtext('p:AssetType',namespaces=NS)=='INS': continue
        u=(da.findtext('p:URI',namespaces=NS) or '').strip()
        if u: jobs.append((pn,k,u))
def get(job):
    pn,k,u=job
    url=re.sub(r'dl=0','dl=1',u) if 'dropbox.com' in u else u
    d=f'/home/claude/sema/dl/{pn}'; os.makedirs(d,exist_ok=True); raw=f'{d}/{k}.raw'
    r=subprocess.run(['curl','-sSL','--max-time','60','-o',raw,'-w','%{http_code} %{content_type}',url],capture_output=True,text=True)
    info=r.stdout.strip()
    try:
        im=Image.open(raw); im.load()
        if im.mode in ('RGBA','LA','P'):
            im=im.convert('RGBA'); bg=Image.new('RGB',im.size,(255,255,255)); bg.paste(im,mask=im.split()[-1]); im=bg
        else: im=im.convert('RGB')
        w,h=im.size; im.thumbnail((800,800)); out=f'{d}/{k}.jpg'; im.save(out,quality=82)
        os.remove(raw)
        return dict(pn=pn,k=k,url=u,ok=True,size=[w,h],md5=hashlib.md5(open(out,'rb').read()).hexdigest(),info=info)
    except Exception as e:
        return dict(pn=pn,k=k,url=u,ok=False,err=str(e)[:80],info=info+' '+r.stderr[:80])
with cf.ThreadPoolExecutor(12) as ex: res=list(ex.map(get,jobs))
json.dump(res,open('/home/claude/sema/dl_results.json','w'),indent=1)
ok=[r for r in res if r['ok']]
print('jobs',len(jobs),'ok',len(ok),'parts needing',len(need),'parts with any download',len({r['pn'] for r in ok}))
import collections
print(collections.Counter(r['info'][:30] for r in res if not r['ok']).most_common(8))
print('dup md5 top', collections.Counter(r['md5'] for r in ok).most_common(5))

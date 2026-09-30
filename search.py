import json,os,re,time
from urllib.parse import urljoin,quote
from playwright.sync_api import sync_playwright,TimeoutError as PlaywrightTimeoutError
from config import *
from mailer import send_report
def load(p,d):
 try:
  with open(p,encoding='utf8') as f:return json.load(f)
 except:return d
def save(p,d):
 with open(p,'w',encoding='utf8') as f:json.dump(d,f,ensure_ascii=False,indent=2)
def ns(s):return re.sub(r'\s+',' ',s or '').strip()
def rent(s):
 s=(s or '').replace(',','').replace('，',''); m=re.search(r'(\d+(?:\.\d+)?)\s*万円',s)
 if m:return int(float(m.group(1))*10000)
 m=re.search(r'(\d{4,7})\s*円',s); return int(m.group(1)) if m else None
def area(s):
 m=re.search(r'(\d+(?:\.\d+)?)\s*(?:m²|m2|㎡)',(s or '').replace(',','')); return float(m.group(1)) if m else None
def layout(s):
 m=re.search(r'(ワンルーム|[1-5][KDK]{1,3}(?:[+＋]S)?|[1-5]LDK)',s or ''); return m.group(1) if m else ''
def pid(p):return 'url:'+p['url'].split('#')[0].rstrip('/') if p.get('url') else 'text:'+ns(p.get('title','')+'|'+p.get('address',''))
def score(p):
 t=' '.join(str(p.get(k,'')) for k in ('title','building_type','description','layout')); sc=0; r=[]
 for w,n in [('戸建',3),('一戸建',3),('貸家',3),('テラスハウス',2),('タウンハウス',2),('木造',2),('SOHO',2),('事務所可',2),('駐車場',1),('ペット',1)]:
  if w in t:sc+=n;r.append(w)
 if p.get('rent') and p['rent']<=60000:sc+=2;r.append('家賃6万円以下')
 elif p.get('rent') and p['rent']<=80000:sc+=1;r.append('家賃8万円以下')
 return '★'*min(sc,5)+'☆'*(5-min(sc,5)),'・'.join(dict.fromkeys(r))
def propish(t):return len(t)>25 and re.search(r'(万円|円)',t) and re.search(r'(m²|m2|㎡|DK|LDK|ワンルーム|1K|1DK|1LDK|2K|2DK|2LDK|3K|3DK|3LDK|4K|4DK|4LDK)',t)
def generic(page,source,area_name):
 out=[];seen=set()
 for a in page.locator('a').all():
  try:
   href=a.get_attribute('href'); title=ns(a.inner_text()); full=urljoin(page.url,href or '')
   if not href or not title:continue
   if source not in full:continue
   if '/chintai/' not in full:continue
   par=a.locator('xpath=ancestor::*[self::article or self::li or self::div][1]'); txt=ns(par.inner_text(timeout=2000))
   if not propish(txt):continue
   key=full.split('#')[0]
   if key in seen:continue
   seen.add(key);out.append({'source':source.upper(), 'area':area_name,'title':title[:120],'rent':rent(txt),'layout':layout(txt),'size':area(txt),'address':'','url':key,'description':txt[:700]})
  except:continue
 return out
def suumo(page,a,u):
 page.goto(u,wait_until='domcontentloaded',timeout=REQUEST_TIMEOUT_MS);page.wait_for_timeout(2500); cards=page.locator('.cassetteitem'); raw=cards.count();out=[]
 for i in range(raw):
  try:
   c=cards.nth(i); txt=ns(c.inner_text()); href=c.locator('a').first.get_attribute('href')
   out.append({'source':'SUUMO','area':a,'title':ns(c.locator('.cassetteitem_content-title').first.inner_text()),'rent':rent(c.locator('.cassetteitem_price--rent').first.inner_text()),'layout':ns(c.locator('.cassetteitem_madori').first.inner_text()),'size':area(c.locator('.cassetteitem_menseki').first.inner_text()),'address':ns(c.locator('.cassetteitem_detail-col1').first.inner_text()),'url':urljoin(page.url,href or ''),'description':txt[:700]})
  except:pass
 return out or generic(page,'suumo',a),raw
def homes(page,a,u):
 page.goto(u,wait_until='domcontentloaded',timeout=REQUEST_TIMEOUT_MS);page.wait_for_timeout(2500);out=[];raw=0
 for sel in ['[data-testid*=property]','article','li']:
  loc=page.locator(sel);raw=max(raw,loc.count())
  for i in range(min(loc.count(),100)):
   try:
    c=loc.nth(i);txt=ns(c.inner_text(timeout=1500))
    if not propish(txt):continue
    aa=c.locator('a').first; href=aa.get_attribute('href') if aa.count() else ''; title=ns(aa.inner_text()) if aa.count() else txt[:100]
    out.append({'source':"HOME'S",'area':a,'title':title[:120],'rent':rent(txt),'layout':layout(txt),'size':area(txt),'address':'','url':urljoin(page.url,href or ''),'description':txt[:700]})
   except:pass
  if out:break
 return out or generic(page,'homes',a),raw
def athome(page,a,u):
 page.goto(u,wait_until='domcontentloaded',timeout=REQUEST_TIMEOUT_MS);page.wait_for_timeout(2500);out=generic(page,'athome',a);return out,len(out)
def dedupe(items):
 d={}
 for p in items:d[pid(p)]=p;p['id']=pid(p)
 return list(d.values())
def main():
 di=[];allp=[]
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True);ctx=b.new_context(user_agent=USER_AGENT,locale='ja-JP');page=ctx.new_page()
  for a,sources in AREAS.items():
   for src,u in sources.items():
    t=time.time();r={'area':a,'source':src,'url':u,'status':'unknown','raw':0,'parsed':0,'error':''}
    try:
     p,raw={'suumo':suumo,'homes':homes,'athome':athome}[src](page,a,u);r['raw']=raw;r['parsed']=len(p)
     if p:r['status']='OK';allp+=p
     else:r['status']='RETRIEVAL_ZERO'
    except PlaywrightTimeoutError as e:r['status']='TIMEOUT';r['error']=str(e)[:300]
    except Exception as e:r['status']='ERROR';r['error']=repr(e)[:500]
    r['seconds']=round(time.time()-t,1);di.append(r);print(json.dumps(r,ensure_ascii=False))
  b.close()
 allp=dedupe(allp);c=[]
 for p in allp:
  if p.get('rent') is None or p['rent']>MAX_RENT:continue
  p['score'],p['comment']=score(p);p['map']='https://www.google.com/maps/search/?api=1&query='+quote(p.get('address') or f"{p.get('area','')} {p.get('title','')}");c.append(p)
 seen=load(SEEN_FILE,{});prices=load(PRICE_FILE,{});new=[];down=[]
 for p in c:
  old=prices.get(p['id'])
  if p['id'] not in seen:new.append(p);p['status']='新着'
  if old is not None and p.get('rent')<old:down.append(p);p['old_price']=old;p['status']='値下げ'
  seen[p['id']]=True;prices[p['id']]=p['rent']
 save(SEEN_FILE,seen);save(PRICE_FILE,prices)
 report={'total_retrieved':len(allp),'candidates':len(c),'new':new,'price_down':down,'sample':c[:10],'diagnostics':di}
 save(DIAG_FILE,report);send_report(report)
if __name__=='__main__':main()
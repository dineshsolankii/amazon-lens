from bs4 import BeautifulSoup
import json
from html import unescape
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, AnyHttpUrl
from fastapi.responses import JSONResponse, HTMLResponse
import requests
from fastapi.middleware.cors import CORSMiddleware

def normalize_amazon_image_url(u):
    if not u:
        return None
    u = u.split('?')[0]
    m = re.match(r"^(https://[^\s]+/images/I/[^.]+)\.[^/]*\.(jpg|png)$", u)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return u

def extract_product_images(soup):
    urls = []
    landing = soup.find(id='landingImage')
    if landing:
        u = landing.get('data-old-hires')
        if u:
            urls.append(u)
        dyn = landing.get('data-a-dynamic-image')
        if dyn:
            try:
                data = json.loads(unescape(dyn))
                best = None
                best_w = -1
                for k, v in data.items():
                    w = v[0] if isinstance(v, (list, tuple)) and v else -1
                    if w > best_w:
                        best_w = w
                        best = k
                if best:
                    urls.append(best)
            except Exception:
                pass
    alt = soup.find(id='altImages')
    if alt:
        for im in alt.select('img[src]'):
            src = im['src']
            n = normalize_amazon_image_url(src)
            urls.append(n or src)

    for s in soup.find_all('script', {'type': 'a-state'}):
        try:
            key_attr = s.get('data-a-state') or ''
            if 'desktop-twister-sort-filter-data' in key_attr:
                data = json.loads(s.string) if s.string else None
                col = (data or {}).get('sortedDimValuesForAllDims', {}).get('color_name', [])
                for item in col:
                    img = ((item or {}).get('imageAttribute') or {}).get('url')
                    if img:
                        urls.append(normalize_amazon_image_url(img))
        except Exception:
            pass

    for s in soup.find_all('script'):
        try:
            txt = s.string or ''
            if not txt or 'colorImages' not in txt:
                continue
            for m in re.finditer(r'"hiRes"\s*:\s*"(https:[^\"]+)"', txt):
                urls.append(normalize_amazon_image_url(m.group(1)))
            for m in re.finditer(r'"large"\s*:\s*"(https:[^\"]+)"', txt):
                urls.append(normalize_amazon_image_url(m.group(1)))
        except Exception:
            pass
    for s in soup.find_all('script', {'type': 'application/ld+json'}):
        try:
            obj = json.loads(s.string) if s.string else None
            if isinstance(obj, dict) and obj.get('@type') == 'Product':
                img = obj.get('image')
                if isinstance(img, list):
                    urls.extend(img)
                elif isinstance(img, str):
                    urls.append(img)
        except Exception:
            continue
    seen = set()
    out = []
    for u in urls:
        if not u:
            continue
        if 'aicid=community-reviews' in u:
            continue
        if '/images/I/' not in u:
            continue
        if 'sprite' in u or 'transparent-pixel' in u:
            continue
        u = normalize_amazon_image_url(u)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

class ExtractRequest(BaseModel):
    amazonUrl: AnyHttpUrl

app = FastAPI()
cache = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.head('/health')
def health_head():
    return JSONResponse({})

@app.post('/extract-images')
def extract_images(req: ExtractRequest):
    url = str(req.amazonUrl)
    if url in cache:
        return {'url': url, 'cached': True, 'images': cache[url]}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        r = requests.get(url, headers=headers, timeout=20)
    except requests.RequestException:
        raise HTTPException(status_code=502, detail='fetch_error')
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail='bad_status')
    imgs = extract_product_images(BeautifulSoup(r.text, 'html.parser'))
    cache[url] = imgs
    return {'url': url, 'cached': False, 'images': imgs}

@app.get('/')
def root(amazonUrl: str | None = None, format: str | None = None):
    if amazonUrl:
        res = extract_images(ExtractRequest(amazonUrl=amazonUrl))
        if format == 'html':
            items = '\n'.join(f"<li><a href='{u}' target='_blank'>{u}</a></li>" for u in res['images'])
            html = f"""<html><body><h1>amazon-lens</h1><p>URL: {res['url']}</p><ul>{items}</ul></body></html>"""
            return HTMLResponse(html)
        return JSONResponse(res)
    return {'service': 'amazon-lens', 'status': 'ok'}

@app.head('/')
def root_head():
    return JSONResponse({})

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app:app', host='0.0.0.0', port=8000, reload=False)

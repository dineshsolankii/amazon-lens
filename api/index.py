from fastapi import FastAPI, HTTPException
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, AnyHttpUrl
from fastapi.responses import JSONResponse
import requests
from bs4 import BeautifulSoup
import json
from html import unescape
import re
from typing import List, Dict
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

class ExtractRequest(BaseModel):
    amazonUrl: AnyHttpUrl

cache: Dict[str, List[str]] = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def normalize_amazon_image_url(u: str) -> str | None:
    if not u:
        return None
    u = u.split('?')[0]
    m = re.match(r"^(https://[^\s]+/images/I/[^.]+)\.[^/]*\.(jpg|png)$", u)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return u

def extract_product_images_from_html(html: str) -> List[str]:
    soup = BeautifulSoup(html, 'html.parser')
    urls: List[str] = []
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
                        urls.append(normalize_amazon_image_url(img) or img)
        except Exception:
            pass
    for s in soup.find_all('script'):
        try:
            txt = s.string or ''
            if not txt or 'colorImages' not in txt:
                continue
            for m in re.finditer(r'"hiRes"\s*:\s*"(https:[^\"]+)"', txt):
                urls.append(normalize_amazon_image_url(m.group(1)) or m.group(1))
            for m in re.finditer(r'"large"\s*:\s*"(https:[^\"]+)"', txt):
                urls.append(normalize_amazon_image_url(m.group(1)) or m.group(1))
        except Exception:
            pass
    seen = set()
    out: List[str] = []
    for u in urls:
        if not u:
            continue
        if 'aicid=community-reviews' in u:
            continue
        if '/images/I/' not in u:
            continue
        if 'sprite' in u or 'transparent-pixel' in u:
            continue
        u2 = normalize_amazon_image_url(u)
        u_final = u2 or u
        if u_final not in seen:
            seen.add(u_final)
            out.append(u_final)
    return out

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.head('/health')
def health_head():
    return JSONResponse({})

@app.get('/')
def root():
    return {
        'service': 'amazon-lens',
        'status': 'ok',
        'endpoints': {
            'health': '/api/index/health',
            'extract_images': '/api/index/extract-images'
        }
    }

@app.post('/extract-images')
def extract_images(req: ExtractRequest):
    url = str(req.amazonUrl)
    if url in cache:
        return JSONResponse({'url': url, 'cached': True, 'images': cache[url]})
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9'
    }
    try:
        r = requests.get(url, headers=headers, timeout=20)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail='fetch_error')
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail='bad_status')
    imgs = extract_product_images_from_html(r.text)
    cache[url] = imgs
    return JSONResponse({'url': url, 'cached': False, 'images': imgs})

# Vercel serverless often mounts this function at /api/index; support POST to root
@app.post('/')
def extract_images_root(req: ExtractRequest):
    return extract_images(req)

@app.get('/extract-images')
def extract_images_get(amazonUrl: str):
    return extract_images(ExtractRequest(amazonUrl=amazonUrl))

@app.api_route('/', methods=['GET'])
def root_info():
    return {
        'service': 'amazon-lens',
        'status': 'ok',
        'endpoints': {
            'health': '/api/index/health',
            'extract_images_post': '/api/index',
            'extract_images_get': '/api/index/extract-images?amazonUrl=<url>'
        }
    }

@app.head('/')
def root_head():
    return JSONResponse({})

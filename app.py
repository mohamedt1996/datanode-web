"""Single-user DataNode resolver and bounded-memory download relay."""
import asyncio
import base64
import contextlib
import hmac
import ipaddress
import os
import re
import secrets
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin, quote, unquote

import aiohttp
from aiohttp import web
from aiohttp.resolver import DefaultResolver
from vendor import moon_extract as moon

ROOT = Path(__file__).parent
TOKEN = os.environ.get('APP_PASSWORD', '')
TTL = 1800
JOBS = {}
LOCK = asyncio.Lock()
GATE = None
ACTIVE = None
FAILURES = {}


def validate_source(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('Enter a DataNode share link.')
    p = urlsplit(url.strip())
    if (p.scheme != 'https' or p.hostname not in ('datanodes.to', 'www.datanodes.to')
            or p.username or p.password or p.port not in (None, 443)
            or not re.fullmatch(r'/[A-Za-z0-9]{8,20}(?:/[^/]*)?/?', p.path)):
        raise ValueError('Use an HTTPS datanodes.to file link, not an account or download page.')
    return 'https://datanodes.to' + p.path


def public_url(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('Invalid upstream URL.')
    try:
        addr = ipaddress.ip_address(p.hostname)
    except ValueError:
        if p.hostname.lower() in ('localhost',) or p.hostname.endswith(('.local', '.internal', '.localhost')):
            raise ValueError('Private destination blocked.')
    else:
        if not addr.is_global:
            raise ValueError('Private destination blocked.')
    return url


class PublicResolver(DefaultResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await super().resolve(host, port, family)
        if not records or any(not ipaddress.ip_address(r['host']).is_global for r in records):
            raise OSError('Private destination blocked')
        return records


@web.middleware
async def protection(request, handler):
    if request.path == '/healthz':
        return web.json_response({'ok': True})
    now = time.monotonic()
    key = request.remote or 'unknown'
    failed = FAILURES.get(key, [])
    failed = [t for t in failed if now - t < 60]
    if len(failed) >= 12:
        raise web.HTTPTooManyRequests(text='Wait one minute before trying again.')
    try:
        scheme, encoded = request.headers.get('Authorization', '').split(' ', 1)
        user, password = base64.b64decode(encoded, validate=True).decode().split(':', 1)
        valid = scheme.lower() == 'basic' and hmac.compare_digest(user.encode(), b'admin') and hmac.compare_digest(password.encode(), TOKEN.encode())
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        if len(FAILURES) > 4096:
            FAILURES.clear()
        FAILURES[key] = failed + [now]
        raise web.HTTPUnauthorized(headers={'WWW-Authenticate': 'Basic realm="DataNode Web", charset="UTF-8"'})
    FAILURES.pop(key, None)
    if request.method == 'POST':
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            raise web.HTTPForbidden(text='Cross-origin request blocked.')
        if request.headers.get('X-Requested-With') != 'DataNodeWeb':
            raise web.HTTPForbidden(text='Missing request header.')
    response = await handler(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    return response


async def index(request):
    return web.FileResponse(ROOT / 'static/index.html')


async def asset(request):
    name = request.match_info['name']
    if name not in ('app.js', 'style.css'):
        raise web.HTTPNotFound()
    return web.FileResponse(ROOT / 'static' / name)


def prune():
    now = time.time()
    for key, job in list(JOBS.items()):
        if job['state'] in ('ready', 'error') and now - job['created'] > TTL:
            JOBS.pop(key, None)


async def create(request):
    prune()
    if sum(j['state'] in ('queued', 'resolving', 'verification') for j in JOBS.values()) >= 5 or len(JOBS) >= 50:
        raise web.HTTPTooManyRequests(text='Queue full. Wait for a download to finish.')
    try:
        data = await request.json()
        url = validate_source(data.get('url'))
    except (ValueError, TypeError, AttributeError):
        raise web.HTTPBadRequest(text='Enter an HTTPS datanodes.to file link.')
    jid = secrets.token_urlsafe(24)
    job = {'id': jid, 'source': url, 'state': 'queued', 'created': time.time(), 'message': 'Waiting for the browser.'}
    JOBS[jid] = job
    job['task'] = asyncio.create_task(resolve(job))
    return web.json_response({'id': jid}, status=202)


async def browser_guard(route):
    """Do not allow web pages to request private network destinations."""
    url = route.request.url
    try:
        if url.startswith(('data:', 'blob:')):
            return await route.continue_()
        public_url(url)
        p = urlsplit(url)
        resolver = PublicResolver()
        try:
            await resolver.resolve(p.hostname, p.port or 443)
        finally:
            await resolver.close()
        await route.continue_()
    except Exception:
        await route.abort()


async def resolve(job):
    global ACTIVE
    async with LOCK:
        ACTIVE = job['id']
        job.update(state='resolving', message='Opening DataNode and preparing your link…')
        try:
            async with asyncio.timeout(540):
                direct = await moon.extract_datanodes_api(job['source'])
                browser = None
                if not direct:
                    browser = await GATE.get()
                    context = await moon._shared_context(browser)
                    await context.route('**/*', browser_guard)
                    try:
                        direct, _ = await moon.extract_datanodes(browser, job['source'], headless=False)
                    finally:
                        await context.unroute('**/*', browser_guard)
                if not direct:
                    raise ValueError('DataNode did not return a download link. Verification may have failed, or the file may be unavailable. Try again.')
                public_url(direct)
                cookies = []
                agent = moon.FALLBACK_UA
                if browser and browser.contexts:
                    context = browser.contexts[0]
                    cookies = await context.cookies([direct])
                    if context.pages:
                        agent = await context.pages[0].evaluate('navigator.userAgent')
                job.update(state='ready', direct=direct, cookies=cookies, agent=agent,
                           message='Your download is ready. Links expire; download now.')
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            job.update(state='error', message='Verification timed out. Submit the link again.')
        except Exception as exc:
            job.update(state='error', message=str(exc) if isinstance(exc, ValueError) else 'The browser or upstream connection failed. Try again; check container logs if it repeats.')
        finally:
            ACTIVE = None


original_solve = moon.dn_solve_turnstile
async def notified_solve(page, headless):
    if ACTIVE in JOBS:
        JOBS[ACTIVE].update(state='verification', message='Trying verification automatically. Open Browser help if it needs a click.')
    return await original_solve(page, headless)
moon.dn_solve_turnstile = notified_solve


def get_job(request):
    prune()
    job = JOBS.get(request.match_info['jid'])
    if not job:
        raise web.HTTPNotFound(text='This link expired. Submit the DataNode link again.')
    return job


async def status(request):
    job = get_job(request)
    return web.json_response({k: job[k] for k in ('id', 'state', 'message')})


async def active_page(request):
    job = get_job(request)
    if ACTIVE != job['id'] or not GATE.opened:
        raise web.HTTPConflict(text='No active browser for this job.')
    browser = await GATE.get()
    for context in browser.contexts:
        for page in reversed(context.pages):
            if urlsplit(page.url).hostname in ('datanodes.to', 'www.datanodes.to'):
                return page
    raise web.HTTPConflict(text='Browser is starting. Try again in a moment.')


async def screenshot(request):
    page = await active_page(request)
    return web.Response(body=await page.screenshot(type='jpeg', quality=65), content_type='image/jpeg')


async def click(request):
    page = await active_page(request)
    try:
        data = await request.json()
        x, y = float(data['x']), float(data['y'])
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError()
        size = await page.evaluate('({w:innerWidth,h:innerHeight})')
        await page.mouse.click(x * size['w'], y * size['h'])
    except (ValueError, KeyError, TypeError):
        raise web.HTTPBadRequest()
    return web.json_response({'ok': True})


async def direct(request):
    job = get_job(request)
    if job['state'] != 'ready':
        raise web.HTTPConflict(text='Link is not ready.')
    raise web.HTTPFound(location=job['direct'], headers={'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store'})


async def relay(request):
    job = get_job(request)
    if job['state'] != 'ready':
        raise web.HTTPConflict(text='Link is not ready.')
    headers = {'User-Agent': job['agent'], 'Referer': 'https://datanodes.to/'}
    if 'Range' in request.headers:
        if not re.fullmatch(r'bytes=\d*-\d*', request.headers['Range']):
            raise web.HTTPBadRequest(text='Use one byte range.')
        headers['Range'] = request.headers['Range']
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), limit=2)
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=120)
    session = aiohttp.ClientSession(connector=connector, timeout=timeout, auto_decompress=False,
                                    cookie_jar=aiohttp.DummyCookieJar(), trust_env=False)
    upstream = None
    started = False
    try:
        target = job['direct']
        for _ in range(6):
            public_url(target)
            hdr = dict(headers)
            # Never forward verification cookies to a different redirected host.
            if urlsplit(target).hostname == urlsplit(job['direct']).hostname:
                cookie = '; '.join(c['name'] + '=' + c['value'] for c in job['cookies'])
                if cookie:
                    hdr['Cookie'] = cookie
            upstream = await session.request(request.method, target, headers=hdr, allow_redirects=False)
            if upstream.status in (301, 302, 303, 307, 308):
                location = upstream.headers.get('Location')
                upstream.release()
                if not location:
                    raise ValueError('Missing redirect')
                target = urljoin(target, location)
                continue
            break
        else:
            raise ValueError('Too many redirects')
        if upstream.status == 416:
            return web.Response(status=416, headers={'Content-Range': upstream.headers.get('Content-Range', 'bytes */0')})
        if upstream.status not in (200, 206) or 'text/html' in upstream.headers.get('Content-Type', '').lower():
            raise web.HTTPBadGateway(text='The file link expired or the host refused it. Return to the app and resolve it again.')
        filename = unquote(urlsplit(target).path.rsplit('/', 1)[-1]) or 'download'
        filename = filename.replace('\r', '').replace('\n', '')[:200]
        out = {k: upstream.headers[k] for k in ('Content-Length', 'Content-Range', 'Accept-Ranges', 'Content-Encoding') if k in upstream.headers}
        out.update({'Content-Type': 'application/octet-stream', 'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe=''), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Accel-Buffering': 'no'})
        response = web.StreamResponse(status=upstream.status, headers=out)
        await response.prepare(request)
        started = True
        async for chunk in upstream.content.iter_chunked(256 * 1024):
            await response.write(chunk)
        await response.write_eof()
        return response
    except web.HTTPException:
        raise
    except (aiohttp.ClientError, OSError, ValueError, TimeoutError):
        if started:
            request.transport.close() if request.transport else None
            return response
        raise web.HTTPBadGateway(text='Could not connect to the download server. Resolve the link again.')
    finally:
        if upstream:
            upstream.close()
        await session.close()


async def lifetime(app):
    global GATE
    moon.configure(lanes=1, captcha_wait=240, headless=False)
    GATE = moon.BrowserGate(['--no-sandbox'], headless=False)
    yield
    tasks = [j['task'] for j in JOBS.values() if not j['task'].done()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await GATE.aclose()
    await moon.close_ff_session()


def make_app():
    app = web.Application(middlewares=[protection], client_max_size=4096)
    app.cleanup_ctx.append(lifetime)
    app.add_routes([web.get('/', index), web.get('/healthz', index), web.get('/static/{name}', asset),
        web.post('/api/jobs', create), web.get('/api/jobs/{jid}', status),
        web.get('/api/jobs/{jid}/screen', screenshot), web.post('/api/jobs/{jid}/click', click),
        web.get('/download/{jid}', relay), web.get('/direct/{jid}', direct)])
    return app

if __name__ == '__main__':
    if len(TOKEN) < 20:
        raise SystemExit('Set APP_PASSWORD to at least 20 characters. Use install.sh to generate it.')
    web.run_app(make_app(), host='0.0.0.0', port=8080, access_log=None)

import asyncio
import gzip
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

POST = {'X-Requested-With': 'DataNodeWeb'}


class Validation(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(app.validate_source('https://datanodes.to/abcdefgh1234/file.zip'), 'https://datanodes.to/abcdefgh1234/file.zip')
        for source in ['https://evil.example/abcdefgh1234', 'https://datanodes.to.evil.test/abcdefgh1234', 'http://datanodes.to/abcdefgh1234',
                       'https://user@datanodes.to/abcdefgh1234', 'https://datanodes.to:444/abcdefgh1234', 'https://datanodes.to/account', None]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                app.validate_source(source)

    def test_private_urls(self):
        for url in ['http://127.0.0.1/a', 'http://[::1]/a', 'http://169.254.169.254/a', 'http://10.2.3.4/a', 'http://localhost/a',
                    'file:///etc/passwd', 'https://foo.local/a', 'https://user:pass@example.com/a']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                app.public_url(url)
        self.assertEqual(app.public_url('https://cdn.example.com/file'), 'https://cdn.example.com/file')

    def test_name(self):
        self.assertEqual(app.name_of('https://datanodes.to/abcdefgh1234/My%20File.rar'), 'My File.rar')
        self.assertEqual(app.name_of('https://datanodes.to/abcdefgh1234'), 'abcdefgh1234')


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app.JOBS.clear()
        self.client = TestClient(TestServer(app.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_no_login(self):
        r = await self.client.get('/')
        self.assertEqual(r.status, 200)
        self.assertIn('Drop your links', await r.text())
        self.assertEqual(r.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn('frame-src', r.headers['Content-Security-Policy'])
        self.assertEqual((await self.client.get('/healthz')).status, 200)

    async def test_csrf_and_invalid_source(self):
        self.assertEqual((await self.client.post('/api/jobs', json={'urls': ['x']})).status, 403)
        self.assertEqual((await self.client.post('/api/jobs', headers={**POST, 'Origin': 'https://evil.example'}, json={'urls': ['x']})).status, 403)
        self.assertEqual((await self.client.post('/api/jobs', headers=POST, json={'urls': ['http://localhost/test']})).status, 400)

    async def test_batch_with_direct_links(self):
        links = {'https://datanodes.to/aaaaaaaa1111/one.rar': 'https://cdn.example.com/one.rar',
                 'https://datanodes.to/bbbbbbbb2222/two.rar': 'https://cdn.example.com/two.rar'}
        with patch.object(app.moon, 'extract_datanodes_api', new=AsyncMock(side_effect=lambda u: links[u])):
            r = await self.client.post('/api/jobs', headers=POST, json={'urls': [*links, 'https://evil.example/x', *links]})
            self.assertEqual(r.status, 202)
            body = await r.json()
            self.assertEqual(len(body['ids']), 2)            # duplicates dropped
            self.assertEqual(len(body['rejected']), 1)       # bad host reported, not fatal
            await asyncio.gather(*(app.JOBS[i]['task'] for i in body['ids']))
        r = await self.client.get('/api/jobs?ids=' + ','.join(body['ids'] + ['gone']))
        jobs = (await r.json())['jobs']
        self.assertEqual([j['state'] for j in jobs], ['ready', 'ready', 'expired'])
        self.assertEqual({j['direct'] for j in jobs[:2]}, set(links.values()))
        self.assertEqual(jobs[0]['name'], 'one.rar')
        self.assertTrue(all('cookies' not in j for j in jobs))
        r = await self.client.get('/direct/' + body['ids'][0], allow_redirects=False)
        self.assertEqual(r.status, 302)

    async def test_old_single_url_still_works(self):
        with patch.object(app.moon, 'extract_datanodes_api', new=AsyncMock(return_value='https://cdn.example.com/f.zip')):
            r = await self.client.post('/api/jobs', headers=POST, json={'url': 'https://datanodes.to/abcdefgh1234'})
            jid = (await r.json())['ids'][0]
            await app.JOBS[jid]['task']
        self.assertEqual(app.JOBS[jid]['state'], 'ready')

    async def test_private_api_result_rejected(self):
        with patch.object(app.moon, 'extract_datanodes_api', new=AsyncMock(return_value='http://127.0.0.1/secret')):
            r = await self.client.post('/api/jobs', headers=POST, json={'urls': ['https://datanodes.to/abcdefgh1234']})
            jid = (await r.json())['ids'][0]
            await app.JOBS[jid]['task']
        self.assertEqual(app.JOBS[jid]['state'], 'error')

    async def _upstream(self, handler):
        upstream = web.Application(); upstream.router.add_get('/file.zip', handler)
        server = TestServer(upstream); await server.start_server()
        app.JOBS['t'] = {'id': 't', 'name': 'file.zip', 'created': app.time.time(), 'state': 'ready',
                         'direct': str(server.make_url('/file.zip')), 'cookies': [], 'agent': 'test',
                         'task': asyncio.create_task(asyncio.sleep(0))}
        return server

    async def test_stream_and_range(self):
        seen = []
        async def file(req):
            seen.append((req.headers.get('Range'), req.headers.get('Accept-Encoding')))
            return web.Response(status=206, body=b'cdef', headers={'Content-Range': 'bytes 2-5/6', 'Accept-Ranges': 'bytes'})
        server = await self._upstream(file)
        try:
            with patch.object(app, 'public_url', side_effect=lambda u: u):
                r = await self.client.get('/download/t', headers={'Range': 'bytes=2-'})
                self.assertEqual(r.status, 206)
                self.assertEqual(await r.read(), b'cdef')
                self.assertEqual(r.headers['Content-Range'], 'bytes 2-5/6')
                self.assertNotIn('Content-Encoding', r.headers)
                self.assertTrue(r.headers['Content-Disposition'].startswith('attachment'))
                self.assertEqual(seen, [('bytes=2-', 'identity')])
        finally:
            await server.close()

    async def test_compressed_reply_refused(self):
        async def file(req):
            return web.Response(body=gzip.compress(b'x' * 1000), headers={'Content-Encoding': 'gzip'})
        server = await self._upstream(file)
        try:
            with patch.object(app, 'public_url', side_effect=lambda u: u):
                self.assertEqual((await self.client.get('/download/t')).status, 502)
        finally:
            await server.close()

    async def test_html_error_page_refused(self):
        async def file(req):
            return web.Response(body=b'<!DOCTYPE html><html>expired</html>', content_type='application/octet-stream')
        server = await self._upstream(file)
        try:
            with patch.object(app, 'public_url', side_effect=lambda u: u):
                self.assertEqual((await self.client.get('/download/t')).status, 502)
        finally:
            await server.close()

    async def test_dns_private_answers(self):
        resolver = app.PublicResolver()
        try:
            with patch('aiohttp.resolver.ThreadedResolver.resolve', new=AsyncMock(return_value=[{'host': '192.168.1.2'}])):
                with self.assertRaises(OSError):
                    await resolver.resolve('cdn.example.com')
        finally:
            await resolver.close()


if __name__ == '__main__':
    unittest.main()

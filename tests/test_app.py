import asyncio
import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

class Validation(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(app.validate_source('https://datanodes.to/abcdefgh1234/file.zip'), 'https://datanodes.to/abcdefgh1234/file.zip')
        for source in ['https://evil.example/abcdefgh1234', 'https://datanodes.to.evil.test/abcdefgh1234', 'http://datanodes.to/abcdefgh1234', 'https://user@datanodes.to/abcdefgh1234', 'https://datanodes.to:444/abcdefgh1234', 'https://datanodes.to/account', None]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                app.validate_source(source)
    def test_private_urls(self):
        for url in ['http://127.0.0.1/a','http://[::1]/a','http://169.254.169.254/a','http://10.2.3.4/a','http://localhost/a','file:///etc/passwd','https://foo.local/a','https://user:pass@example.com/a']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                app.public_url(url)
        self.assertEqual(app.public_url('https://cdn.example.com/file'), 'https://cdn.example.com/file')

class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app.TOKEN='test-password-that-is-long-enough'
        app.JOBS.clear(); app.FAILURES.clear()
        self.client=TestClient(TestServer(app.make_app()))
        await self.client.start_server()
        self.auth={'Authorization':'Basic '+base64.b64encode(('admin:'+app.TOKEN).encode()).decode()}
        self.post={**self.auth,'X-Requested-With':'DataNodeWeb'}
    async def asyncTearDown(self):
        await self.client.close()
    async def test_auth_and_page(self):
        self.assertEqual((await self.client.get('/')).status,401)
        self.assertEqual((await self.client.get('/healthz')).status,200)
        r=await self.client.get('/',headers=self.auth)
        self.assertEqual(r.status,200)
        self.assertIn('Drop a link',await r.text())
        self.assertEqual(r.headers['X-Frame-Options'],'DENY')
    async def test_csrf_and_invalid_source(self):
        self.assertEqual((await self.client.post('/api/jobs',headers=self.auth,json={'url':'x'})).status,403)
        self.assertEqual((await self.client.post('/api/jobs',headers={**self.post,'Origin':'https://evil.example'},json={'url':'x'})).status,403)
        self.assertEqual((await self.client.post('/api/jobs',headers=self.post,json={'url':'http://localhost/test'})).status,400)
    async def test_resolve_and_redirect(self):
        with patch.object(app.moon,'extract_datanodes_api',new=AsyncMock(return_value='https://cdn.example.com/file.zip')):
            r=await self.client.post('/api/jobs',headers=self.post,json={'url':'https://datanodes.to/abcdefgh1234'})
            self.assertEqual(r.status,202)
            jid=(await r.json())['id']
            await app.JOBS[jid]['task']
        r=await self.client.get('/api/jobs/'+jid,headers=self.auth)
        s=await r.json(); self.assertEqual(s['state'],'ready'); self.assertNotIn('cookies',s)
        r=await self.client.get('/direct/'+jid,headers=self.auth,allow_redirects=False)
        self.assertEqual(r.status,302); self.assertEqual(r.headers['Location'],'https://cdn.example.com/file.zip')
    async def test_private_api_result_rejected(self):
        with patch.object(app.moon,'extract_datanodes_api',new=AsyncMock(return_value='http://127.0.0.1/secret')):
            r=await self.client.post('/api/jobs',headers=self.post,json={'url':'https://datanodes.to/abcdefgh1234'})
            jid=(await r.json())['id']; await app.JOBS[jid]['task']
            self.assertEqual(app.JOBS[jid]['state'],'error')
    async def test_stream_and_range(self):
        seen=[]
        async def file(req):
            seen.append(req.headers.get('Range'))
            return web.Response(status=206,body=b'cdef',headers={'Content-Range':'bytes 2-5/6','Accept-Ranges':'bytes'})
        upstream=web.Application();upstream.router.add_get('/file.zip',file)
        server=TestServer(upstream);await server.start_server()
        try:
            app.JOBS['test']={'id':'test','created':app.time.time(),'state':'ready','direct':str(server.make_url('/file.zip')),'cookies':[],'agent':'test','task':asyncio.create_task(asyncio.sleep(0))}
            # Only this test removes public URL validation to use a local fixture.
            with patch.object(app,'public_url',side_effect=lambda u:u):
                r=await self.client.get('/download/test',headers={**self.auth,'Range':'bytes=2-'})
                self.assertEqual(r.status,206)
                self.assertEqual(await r.read(),b'cdef')
                self.assertEqual(r.headers['Content-Range'],'bytes 2-5/6')
                self.assertTrue(r.headers['Content-Disposition'].startswith('attachment'))
                self.assertEqual(seen,['bytes=2-'])
        finally:
            await server.close()
    async def test_dns_private_answers(self):
        resolver=app.PublicResolver()
        try:
            with patch('aiohttp.resolver.ThreadedResolver.resolve',new=AsyncMock(return_value=[{'host':'192.168.1.2'}])):
                with self.assertRaises(OSError): await resolver.resolve('cdn.example.com')
        finally:
            await resolver.close()

if __name__=='__main__':unittest.main()

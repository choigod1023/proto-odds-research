"""Real Nginx + local fake collector. Set NGINX_BIN; CI must run this suite."""
import gzip
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
NGINX = os.environ.get('NGINX_BIN') or shutil.which('nginx')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(NGINX, 'NGINX_BIN required (mandatory in edge CI job)')
class EdgeIntegration(unittest.TestCase):
    def test_real_cache_failure_recovery_and_revision_isolation(self):
        state = {'status': 200, 'revision': 'first', 'requests': 0, 'posts': 0}

        class Collector(BaseHTTPRequestHandler):
            def do_GET(self):
                state['requests'] += 1
                status = state['status']
                if 'revision=wrong' in self.path:
                    status = 409
                body = json.dumps({'revision': state['revision'], 'generated_at': 'original',
                                   'path': self.path.split('&_=')[0]}).encode()
                compressed = self.headers.get('Accept-Encoding') == 'gzip'
                if compressed:
                    body = gzip.compress(body)
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'public, max-age=5')
                self.send_header('Vary', 'Accept-Encoding')
                if compressed:
                    self.send_header('Content-Encoding', 'gzip')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                state['posts'] += 1
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.send_response(201)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *args):
                pass

        origin = ThreadingHTTPServer(('127.0.0.1', 0), Collector)
        threading.Thread(target=origin.serve_forever, daemon=True).start()
        port = free_port()
        with tempfile.TemporaryDirectory(prefix='edge-integration-') as directory:
            base = Path(directory)
            (base / 'logs').mkdir()
            (base / 'temp').mkdir()
            config = (ROOT / 'deploy/web/nginx.conf').read_text(encoding='utf-8')
            # Only OS paths, ports, and test upstream differ from production.
            config = config.replace('/dev/stderr', 'logs/error.log')
            config = config.replace('/tmp/nginx.pid', 'logs/nginx.pid')
            config = config.replace('/var/cache/nginx/', base.as_posix() + '/cache/')
            config = config.replace('listen 8080;', f'listen 127.0.0.1:{port};')
            config = config.replace('https://proto-odds-collector.fly.dev',
                                    f'http://127.0.0.1:{origin.server_port}')
            config = config.replace('proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;', '')
            config = config.replace('proxy_ssl_verify on;', 'proxy_ssl_verify off;')  # local HTTP fixture only
            (base / 'cache/responses').mkdir(parents=True)
            (base / 'nginx.conf').write_text(config, encoding='utf-8')
            args = [NGINX, '-p', base.as_posix() + '/', '-c', 'nginx.conf']
            check = subprocess.run([*args, '-t'], capture_output=True)
            self.assertEqual(check.returncode, 0, check.stderr.decode(errors='replace'))
            child = subprocess.Popen([*args, '-g', 'daemon off;'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            def get(path, encoding='identity', data=None):
                request = Request(f'http://127.0.0.1:{port}' + path, data=data,
                                  headers={'Accept-Encoding': encoding})
                try:
                    response = urlopen(request, timeout=10)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, response.headers, response.read()

            try:
                for attempt in range(50):
                    try:
                        self.assertEqual(get('/readyz')[0], 200)
                        break
                    except OSError:
                        time.sleep(.1)
                else:
                    self.fail('Nginx failed to start')
                recent = '/api/matches?scope=recent'
                first = get(recent + '&_=1')
                self.assertEqual(first[0], 200)
                self.assertEqual(first[1]['X-Response-Cache'], 'MISS')
                count = state['requests']
                hit = get(recent + '&_=2')
                self.assertEqual(hit[1]['X-Response-Cache'], 'HIT')
                self.assertEqual(hit[2], first[2])
                self.assertEqual(state['requests'], count)
                self.assertEqual(get('/api/matches?scope=all')[1]['X-Response-Cache'], 'MISS')
                compressed = get(recent, 'gzip')
                self.assertEqual(compressed[1]['Content-Encoding'], 'gzip')
                self.assertEqual(json.loads(gzip.decompress(compressed[2]))['revision'], 'first')
                detail = '/api/match-detail?key=game1&revision=first'
                self.assertEqual(get(detail)[0], 200)
                self.assertEqual(get(detail)[1]['X-Response-Cache'], 'HIT')
                self.assertEqual(get('/api/match-detail?key=game1&revision=wrong')[0], 409)
                self.assertEqual(get('/api/match-detail?key=game2&revision=first')[1]['X-Response-Cache'], 'MISS')
                self.assertEqual(get('/anonymous-bets', data=b'{}')[0], 201)
                self.assertEqual(get('/anonymous-bets', data=b'{}')[0], 201)
                self.assertEqual(state['posts'], 2)
                self.assertEqual(get('/not-public')[0], 404)
                state['status'] = 503
                time.sleep(6.2)  # Nginx expiry is rounded to whole seconds.
                started = time.monotonic()
                stale = get(recent)
                stale_seconds = time.monotonic() - started
                self.assertLess(stale_seconds, 2)
                self.assertEqual(stale[0], 200)
                self.assertIn(stale[1]['X-Response-Cache'], ('STALE', 'UPDATING'))
                self.assertEqual(stale[2], first[2])
                self.assertEqual(stale[1]['Cache-Control'], 'no-store')
                self.assertEqual(get('/health')[0], 503)
                self.assertEqual(get('/api/loss-grades')[0], 503)
                state.update(status=200, revision='second')
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if json.loads(get(recent)[2])['revision'] == 'second':
                        break
                    time.sleep(.2)
                else:
                    self.fail('next background refresh did not publish new response')
                # Recovery must not retain a cached 503 on a previously cold path.
                self.assertEqual(get('/api/loss-grades')[0], 200)
                # A process restart with retained cache disk must still serve it.
                subprocess.run([*args, '-s', 'quit'], check=True, capture_output=True)
                child.wait(timeout=15)
                state['status'] = 503
                child = subprocess.Popen([*args, '-g', 'daemon off;'],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for attempt in range(50):
                    try:
                        restarted = get(recent)
                        break
                    except OSError:
                        time.sleep(.1)
                self.assertEqual(restarted[0], 200)
                self.assertEqual(json.loads(restarted[2])['revision'], 'second')
                print(f'edge integration: stale_response_seconds={stale_seconds:.4f}; '
                      'origin_503_preserved_body=true; refresh_recovered=true; restart_cache=true')
            finally:
                subprocess.run([*args, '-s', 'quit'], capture_output=True, timeout=10)
                child.wait(timeout=15)
                origin.shutdown()
                origin.server_close()


if __name__ == '__main__':
    unittest.main()

"""Run with python -m dashboard.server. Loopback-only, read-only local service."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import traceback
from urllib.parse import parse_qs, urlparse

from dashboard.service import Dashboard, clean

STATIC = Path(__file__).parent / 'static'


def handler(service):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            host = self.headers.get('Host', '').split(':')[0]
            if host not in ('127.0.0.1', 'localhost'):
                self.send_error(403)
                return
            url = urlparse(self.path)
            params = parse_qs(url.query)
            book = params.get('bookmaker', ['draftkings'])[0]
            if len(book) > 80:
                self.send_error(400)
                return
            try:
                if url.path.startswith('/api/'):
                    if url.path == '/api/overview':
                        data = service.overview(book)
                    elif url.path == '/api/game':
                        data = service.game(params.get('id', [''])[0], book)
                    elif url.path == '/api/team':
                        data = service.team(params.get('id', [''])[0])
                    elif url.path == '/api/elo':
                        teams = [team.strip().upper() for team in params.get('teams', [''])[0].split(',') if team.strip()]
                        data = service.elo(teams)
                    elif url.path == '/api/performance':
                        data = service.performance(params.get('policy', [None])[0])
                    elif url.path == '/api/research':
                        data = service.research()
                    else:
                        self.send_error(404)
                        return
                    body = json.dumps(clean(data), allow_nan=False).encode()
                    content_type = 'application/json; charset=utf-8'
                else:
                    relative = 'index.html' if url.path == '/' else url.path.lstrip('/')
                    path = (STATIC / relative).resolve()
                    if not path.is_relative_to(STATIC.resolve()) or not path.is_file():
                        self.send_error(404)
                        return
                    body = path.read_bytes()
                    content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(body)
            except KeyError:
                self.send_error(404, 'Record not found')
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                traceback.print_exc()
                self.send_error(500, 'Unable to read project data. Check the server log.')

        def do_POST(self):
            self.send_error(405, 'Dashboard is read-only')

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--model', type=Path)
    args = parser.parse_args()
    kwargs = {'model_path': args.model}
    if args.db:
        kwargs['db_path'] = args.db
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(Dashboard(**kwargs)))
    print(f'NBA dashboard: http://127.0.0.1:{args.port} (read-only)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

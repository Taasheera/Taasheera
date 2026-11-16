"""Explicit local QA email sink; never included in the production application.

Messages stay in the isolated preview folder. The viewer binds only to loopback,
uses synthetic test accounts, and never contacts Gmail/SES or prints codes in logs.
"""
import argparse
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import time


class FileResetSender:
    def __init__(self, directory):
        self.directory = Path(directory)

    def send_reset(self, recipient: str, code: str):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = secrets.token_hex(16)
        temporary = self.directory / (name + '.tmp')
        final = self.directory / (name + '.json')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'recipient': recipient, 'code': code, 'created_at': int(time.time()),
                       'expires_at': int(time.time()) + 600}, stream)
        temporary.replace(final)


def messages(directory):
    result = []
    for file in Path(directory).glob('*.json'):
        try:
            row = json.loads(file.read_text())
            if set(row) == {'recipient', 'code', 'created_at', 'expires_at'}:
                result.append(row)
        except (OSError, ValueError): pass
    return sorted(result, key=lambda row: row['created_at'], reverse=True)[:30]


def serve(directory, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass

        def do_GET(self):
            if self.path != '/':
                self.send_error(404); return
            rows = messages(directory)
            cards = []
            for row in rows:
                sent = datetime.fromtimestamp(row['created_at'], timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                state = 'Expired — request a fresh code' if row['expires_at'] <= time.time() else 'Expires after 10 minutes'
                cards.append(f'<article><h2>{escape(str(row["recipient"]))}</h2><p>{sent}</p>'
                             f'<code>{escape(str(row["code"]))}</code><p>{state}</p></article>')
            page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                '<meta http-equiv="refresh" content="5"><title>Taasheera local QA inbox</title>'
                '<style>body{font:17px system-ui;max-width:760px;margin:40px auto;padding:20px;background:#f4f7f5;color:#173e34}'
                'article{background:white;border:1px solid #b9d5ca;padding:20px;margin:18px 0;border-radius:12px}'
                'h2{font-size:18px;overflow-wrap:anywhere}code{font-size:32px;letter-spacing:5px}</style>'
                '<h1>Local QA inbox</h1><p>Preview emails only. Nothing is sent to Gmail or SES. Newest first; refreshes every five seconds.</p>'
                + (''.join(cards) if cards else '<p>No preview reset emails yet. Request one on the sign-in page.</p>') + '</html>')
            body = page.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(body)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--port', type=int, default=8025)
    options = parser.parse_args()
    serve(options.directory, options.port)

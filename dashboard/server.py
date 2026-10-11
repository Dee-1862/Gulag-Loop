"""Local read-only dashboard: python -m dashboard.server [--demo] [--port 8765]."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from threading import Lock
import time
from dashboard.demo import DemoSource

ROOT = Path(__file__).resolve().parents[1]

def configuration():
    values={}
    path=ROOT/'.env'
    try:
        from dotenv import dotenv_values
    except ImportError:
        pass  # Keep the dependency-free demo available before setup.
    else:
        return {**dotenv_values(path), **os.environ}
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1)
                values[key.strip()]=value.strip().strip('\"\x27')
    return {**values,**os.environ}

class Backend:
    def __init__(self, demo=False):
        self.default_demo=demo
        self.demo=DemoSource()
        self.live=None
        self.error='MongoDB is not configured. Set MONGODB_URI in .env or open the demo preview.'
        self.cache={}
        self.lock=Lock()
        config=configuration()
        if config.get('MONGODB_URI'):
            try:
                from dashboard.data import MongoSource
                self.live=MongoSource(config['MONGODB_URI'],config.get('MONGODB_DB','zombieloop'))
            except ImportError:
                self.error='Install requirements-dashboard.txt to connect to MongoDB.'
            except Exception:
                self.error='MongoDB configuration could not be loaded. Check MONGODB_URI in .env.'
    def source(self, demo):
        return self.demo if demo else self.live
    def snapshot(self, experiment, demo):
        if demo:
            return self.demo.snapshot(experiment)
        if self.live is None:
            return dict(mode='live',connected=False,error=self.error)
        if not experiment:
            self.live.db.command('ping')
            return dict(mode='live',connected=True,error='Select an experiment to inspect its pipeline.')
        with self.lock:
            cached=self.cache.get(experiment)
            if cached and time.monotonic()-cached[0]<2:
                return cached[1]
            result=self.live.snapshot(experiment)
            self.cache[experiment]=(time.monotonic(),result)
            return result

def handler_for(backend):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def send(self, data, content_type='application/json', status=200):
            if not isinstance(data,bytes):
                data=json.dumps(data,allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            self.wfile.write(data)
        def send_video(self, data):
            size=len(data)
            rng=self.headers.get('Range','')
            start,end,status=0,size-1,200
            if rng.startswith('bytes='):
                a,_,b=rng[6:].split(',')[0].partition('-')
                if a:
                    start=int(a); end=int(b) if b else size-1
                else:
                    start=max(0,size-int(b))
                end=min(end,size-1)
                status=206
            self.send_response(status)
            self.send_header('Content-Type','video/mp4')
            self.send_header('Accept-Ranges','bytes')
            self.send_header('Content-Length',str(end-start+1))
            if status==206:
                self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.send_header('Cache-Control','private, max-age=600')
            self.end_headers()
            self.wfile.write(data[start:end+1])
        def do_GET(self):
            url=urlparse(self.path)
            args=parse_qs(url.query)
            demo=args.get('mode',['demo' if backend.default_demo else 'live'])[0]=='demo'
            experiment=args.get('experiment',[''])[0]
            static={'/':('dashboard/index.html','text/html'), '/app.js':('dashboard/app.js','text/javascript'),
                    '/style.css':('dashboard/style.css','text/css'), '/demo-frame.jpg':('docs/img/hud_danger_close.jpg','image/jpeg')}
            if url.path in static:
                path,mime=static[url.path]
                self.send((ROOT/path).read_bytes(),mime)
                return
            try:
                if url.path=='/api/config':
                    self.send(dict(default_mode='demo' if backend.default_demo else 'live'))
                elif url.path=='/api/experiments':
                    source=backend.source(demo)
                    self.send(dict(experiments=source.experiments() if source else []))
                elif url.path=='/api/snapshot':
                    self.send(backend.snapshot(experiment,demo))
                elif url.path=='/api/video':
                    video=backend.live.video(experiment, args.get('job',[''])[0]) if backend.live and not demo else None
                    if not video:
                        self.send(b'', 'video/mp4', 404)
                        return
                    self.send_video(video)
                elif url.path=='/api/frame':
                    frame=backend.live.frame(experiment, args.get('worker',[''])[0], args.get('job',[''])[0]) if backend.live and not demo else None
                    self.send(frame or b'', 'image/jpeg', 200 if frame else 204)
                else:
                    self.send(dict(error='Not found'),status=404)
            except LookupError:
                self.send(dict(mode='live',connected=True,error='Experiment not found. Refresh the experiment list.'),status=404)
            except Exception as exc:
                # Never leak connection strings, credentials or provider errors.
                self.send(dict(mode='live',connected=False,error='MongoDB could not be read. Check connectivity and database permissions.',error_type=type(exc).__name__),status=503)
    return Handler

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo',action='store_true')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(Backend(args.demo)))
    print(f'Dashboard: http://127.0.0.1:{args.port} ({"demo" if args.demo else "live"} mode)',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__=='__main__':
    main()

#!/usr/bin/python
# coding=utf-8
import asyncpg
import asyncio
import tornado
import tornado.ioloop
import tornado.web
import tornado.httpserver
import tornado.options
from functools import partial
import time
import hashlib 
import datetime
import sys
from legacy import legacy_hashes
from secret import secret
import signal

DOCKER = True
MAX_WAIT_SECONDS_BEFORE_SHUTDOWN = 3

def sig_handler(server, sig, frame):
    io_loop = tornado.ioloop.IOLoop.instance()

    def stop_loop(deadline):
        now = time.time()
        if now < deadline:
            io_loop.add_timeout(now + 1, stop_loop, deadline)
        else:
            io_loop.stop()

    def shutdown():
        server.stop()
        stop_loop(time.time() + MAX_WAIT_SECONDS_BEFORE_SHUTDOWN)

    io_loop.add_callback_from_signal(shutdown)
    
    

if not DOCKER:
    connstr = 'postgresql://xnat:xnat@127.0.0.1/xnat'
    f = sys.stdout
else:
    connstr = 'postgresql://xnat:xnat@xnat-db/xnat'
    f = open('/data/xnat/home/logs/hasher_log.txt', 'a')
    sys.stdout = f
    sys.stderr = f
def process(arg):
    oldhash = hashlib.sha1(arg.encode('ascii', 'ignore')).hexdigest()
    if oldhash in legacy_hashes:
        return oldhash #hack         
    fh = hashlib.sha1((arg+secret).encode('ascii', 'ignore')).hexdigest()
    return hashlib.sha1((arg+fh+secret).encode('ascii', 'ignore')).hexdigest()
class MainHandler(tornado.web.RequestHandler):
    async def get(self, arg):
        f.write(str(datetime.datetime.now()) + ': Main : '+ arg+'\n')
        f.flush()
        self.write(process(arg))
class SplitDotHandler(tornado.web.RequestHandler):
    async def get(self, arg):
        f.write(str(datetime.datetime.now()) + ': SplitDot : '+ arg+'\n')
        f.flush()
        self.write(arg.split('.')[0])

class CheckedMainHandler(tornado.web.RequestHandler):
    async def get(self, arg):
        #conn = await asyncpg.connect(connstr)
        try:
            conn = await self.application.pool.acquire()
            f.write(str(datetime.datetime.now()) + ': CheckedMain : '+ arg+'\n')
            f.flush()        
            row = await conn.fetchrow("select new_patient_id, project from iap_sessions_to_share WHERE patient_id = $1 AND (status = 'PENDING' OR status = 'PENDINGSHARE' OR status = 'DICOMSENT' or status = 'DICOMSENDING' ) ORDER BY new_patient_id ASC, CREATED DESC LIMIT 1", arg)   
            row2 = await conn.fetchrow("select new_accession, project from iap_sessions_to_share WHERE accession = $1 AND (status = 'PENDING' OR status = 'PENDINGSHARE' OR status = 'DICOMSENT' or status = 'DICOMSENDING' ) ORDER BY new_accession ASC, CREATED DESC LIMIT 1", arg) 
            if row or row2:
                if row and row['new_patient_id']: 
                    self.write('ALF'+'_'+row['project']+'_'+row['new_patient_id'])
                elif row2 and row2['new_accession']: 
                    self.write('ALF'+'_'+row2['project']+'_'+row2['new_accession']) #if a specified accession is requested the project name
                else:
                    self.write(process(arg))
            else:
                self.write(arg)
        except Exception as e:
            raise e 
        finally:
            await self.application.pool.release(conn)
        
def make_app():
    app = tornado.web.Application([
        (r"/splitdot/(.*)", SplitDotHandler),
        (r"/check/(.*)", CheckedMainHandler),
        (r"/(.*)", MainHandler),
    ])
    pool = asyncpg.create_pool(connstr, max_size=512)
    asyncio.get_event_loop().run_until_complete(pool._async__init__())
    app.pool = pool 
    return app

if __name__ == "__main__":
    app = make_app()
    server = tornado.httpserver.HTTPServer(app)
    server.listen(8888)
    
    signal.signal(signal.SIGTERM, partial(sig_handler, server))
    signal.signal(signal.SIGINT, partial(sig_handler, server))
    
    tornado.ioloop.IOLoop.current().start()

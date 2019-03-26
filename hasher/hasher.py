#!/usr/bin/python
# coding=utf-8
import asyncpg
import asyncio
import tornado
import tornado.ioloop
import tornado.web
import hashlib 
import datetime
import sys
from legacy import legacy_hashes
from secret import secret
DOCKER = True

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
        f.write(str(datetime.datetime.now()) + ': '+ arg+'\n')
        f.flush()
        self.write(process(arg))

        
class CheckedMainHandler(tornado.web.RequestHandler):
    async def get(self, arg):
        #conn = await asyncpg.connect(connstr)
        try:
            conn = await self.application.pool.acquire()
            f.write(str(datetime.datetime.now()) + ': '+ arg+'\n')
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
        (r"/check/(.*)", CheckedMainHandler),
        (r"/(.*)", MainHandler),
    ])
    pool = asyncpg.create_pool(connstr, max_size=512)
    asyncio.get_event_loop().run_until_complete(pool._async__init__())
    app.pool = pool 
    return app

if __name__ == "__main__":
    app = make_app()
    app.listen(8888)
    tornado.ioloop.IOLoop.current().start()

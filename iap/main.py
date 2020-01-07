import asyncpg
import asyncio
import tornado
import tornado.ioloop
import tornado.web
from tornado.httpclient import AsyncHTTPClient
from tornado.log import enable_pretty_logging
import json
import time
import datetime
import uuid
import jwt
import csv
import ldap3
import traceback
from functools import wraps
from requests_threads import AsyncSession
connstr = 'postgresql://xnat:xnat@xnat-db/xnat'

class BaseHandler(tornado.web.RequestHandler):
    def initialize(self, secret, ldap_uri, ldap_base):
        self.secret = secret
        self.ldap_uri = ldap_uri
        self.ldap_base = ldap_base
    def get_current_user(self):
        try:
            token = self.request.headers['Authorization']
            data = jwt.decode(token, self.secret, algorithm='HS256')
            return data['user']
        except Exception:
            pass
        try:
            token = self.get_secure_cookie('token')
            data = jwt.decode(token, self.secret, algorithm='HS256')
            return data['user']
        except:
            pass
        return None
        
        
def auth_rpc(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.current_user:
            raise HTTPError(403)
        return func(self, *args,**kwargs)
    return wrapper
class RPCHandler(BaseHandler):
    """For non-human requests, use an Authorization header"""
    def check_xsrf_cookie(self):
        pass # disable the check

    @auth_rpc
    def get(self):
        self.write({'foo':'bar'})
        
class LoginHandler(BaseHandler):
    """Login page for humans"""
    def get(self):
        self.write(open('templates/login.html', 'r').read().replace('%XSRF%', '')) #to enable xsrf in the future use this self.xsrf_form_html() 

    def post(self):
        username = self.get_argument("username")
        password = self.get_argument("password")
        try:
            # will fail if invalid            
            s = ldap3.Server(self.ldap_uri)
            c = ldap3.Connection(s, username+'@baysidehealth.intra', password)
            if c.bind():
                pass
            else:
                raise Exception('Not authenticated: '+str(c.result))
        except Exception:
            self.write(traceback.format_exc())

        else:
            # successful login
            token = jwt.encode({'user':username}, self.secret, algorithm='HS256')
            self.set_secure_cookie('token', token)
            self.redirect("/index.html")

            
class IndexHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        self.write(open('templates/index.html', 'r').read().replace('%XSRF%', '')) #to enable xsrf in the future use this self.xsrf_form_html() 


async_session = AsyncSession(n=100)
class LinkageHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, project_id):
        global connstr
        global async_session
        try:
            conn = await asyncpg.connect(connstr)
            rows = await conn.fetch('SELECT * FROM iap_sessions_to_share WHERE project = $1', project_id)
            ret = []
            client = AsyncHTTPClient()
            for row in rows:
               hashed = await client.fetch('http://xnat-hasher:8888/{}'.format(row['accession']))
               ret.append([row['accession'], hashed.body.decode('ascii', 'ignore')])
            self.write('<br>'.join(['\t'.join(r) for r in ret]))
        finally:
            await conn.close() 
            
class DeleteHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, request_id):
        global connstr
        global async_session
        conn = await asyncpg.connect(connstr)
        rows = await conn.execute('DELETE FROM iap_sessions_to_share WHERE request_id=$1', request_id)
        await conn.close() 
        
class MainHandler(BaseHandler):
    async def prepare(self):
        self.post_data = {}
        if self.request.body:
            try:
                json_data = tornado.escape.json_decode(self.request.body)
                self.post_data.update(json_data)
            except ValueError:
                pass
                
        self.post_data.update({
            key: [val.decode('utf8') for val in val_list] if len(val_list) > 1 else val_list[0].decode('utf8')
            for key, val_list in self.request.arguments.items()
        })
        self.post_data['errors'] = []
        
        if 'to_download' not in self.post_data:
            self.post_data['to_download'] = []
        if 'override' not in self.post_data:
            self.post_data['override'] = False
        else:
            self.post_data['override'] = True
            
        contents = None
        if 'uploaded_file' in self.request.files:
            uploaded_csv_file = self.request.files['uploaded_file'][0]
            contents = uploaded_csv_file['body'].decode("utf-8").split('\n')
        
        if 'url' in self.post_data:
            future1 = tornado.ioloop.IOLoop.current().run_in_executor(None, requests.get, self.post_data['url'])
            response = await future1
            if len(response.text) > 0: contents = response.text.split('\n')
        if contents:
            for row in csv.reader(contents):
                if len(row) >= 4:
                    self.post_data['to_download'].append([row[0], row[1], row[2], row[3]])
                elif len(row) >= 2:
                    self.post_data['to_download'].append([row[0], row[1], None, None])
                
        for i in range(1,1000):
            s = '{:0>4d}'.format(i)
            try:
                if 'acc'+s in self.post_data and 'pid'+s in self.post_data:
                    temp = []
                    if len(self.post_data['acc'+s].strip()) > 0 and len(self.post_data['pid'+s].strip()) > 0:
                        temp.extend([self.post_data['acc'+s],self.post_data['pid'+s]])
                        if len(self.post_data['nacc'+s].strip()) > 0 and len(self.post_data['npid'+s].strip()) > 0:
                            temp.extend([self.post_data['nacc'+s],self.post_data['npid'+s]])
                        else:
                            temp.extend([None, None])
                    if len(temp) > 0:
                        self.post_data['to_download'].append(temp)
                else:
                    break
            except:
                self.post_data['errors'].append(traceback.format_exc())
                break
        for i, k in enumerate(self.post_data['to_download']):
            acc = self.post_data['to_download'][i][0].replace('AH', '').replace('ALF', '').replace('SDMH', '')
            ptid = self.post_data['to_download'][i][1].replace('AH', '').replace('ALF', '').replace('SDMH', '')
            if len(self.post_data['to_download'][i]) >= 4:
                nacc = self.post_data['to_download'][i][2]
                nptid = self.post_data['to_download'][i][3]
            else:
                nacc,nptid = None, None
            if nacc: 
                nacc = nacc.strip()
                if len(nacc) == 0: nacc = None
            if nptid: 
                nptid = nptid.strip()
                if len(nptid) == 0: nptid = None
            ptid = '0' * (7 - len(ptid)) + ptid
            self.post_data['to_download'][i] = (acc, ptid, nacc, nptid)
        
        self.post_data['to_download'] = list(set(self.post_data['to_download']))
        print (self.post_data)
    def set_default_headers(self):
        """Set the default response header to be JSON."""
        self.set_header("Content-Type", 'application/json; charset="utf-8"')

    def send_response(self, data, status=200):
        """Construct and send a JSON response with appropriate status code."""
        self.set_status(status)
        if data:
            self.write(json.dumps(data))
    
    @tornado.web.authenticated
    async def get(self, location=None):
        start = time.time()
        conn = await asyncpg.connect(connstr)
        row = await conn.fetchrow('SELECT COUNT(*) FROM iap_sessions_to_share WHERE request_id = $1', location)
        total = int(row['count'])
        row = await conn.fetchrow("SELECT COUNT(*) FROM iap_sessions_to_share WHERE request_id = $1 AND status = 'COMPLETED'", location)
        completed = int(row['count'])
        row = await conn.fetchrow("SELECT COUNT(*) FROM iap_sessions_to_share WHERE request_id = $1 AND (status = 'PENDING' OR status = 'DICOMSENT' or status = 'DICOMSENDING' or status = 'RETRIEVED')", location)
        pending = int(row['count'])
        row = await conn.fetchrow("SELECT COUNT(*) FROM iap_sessions_to_share WHERE request_id = $1 AND status = 'FAILED'", location)
        failed = int(row['count'])
        await conn.close()
        self.send_response({'Total':total, 'Completed':completed, 'Pending':pending, 'Failed':failed})
    
    @tornado.web.authenticated
    async def post(self, location=None):
        global connstr
        start = time.time()
        reqid = str(uuid.uuid4()).replace('-','')
        
        
        if 'project' in self.post_data and len(self.post_data['project'].strip()) > 0:
            project = self.post_data['project']
        else:
            project = 'Alfred' #str(uuid.uuid4()).replace('-','')
            
        
        conn = await asyncpg.connect(connstr)
        dt = datetime.datetime.now()
        override = self.post_data['override']
        
        objects = [
                (d[1], d[0], self.post_data['application_entity'], project, 'PENDING', reqid, dt, '', dt, d[3],d[2], override)
                for d in self.post_data['to_download'] if len(d[1]) >= 3 and len(d[0]) >= 3
            ]
        # print (objects)
            
        result = await conn.copy_records_to_table('iap_sessions_to_share', records=objects)

        
        await conn.close()
        location = '/api/'+reqid
        self.set_header("Location", location)
        self.post_data['request_id'] = reqid
        self.post_data['location'] = location
        self.send_response(self.post_data, status=202)
            
        
if __name__ == "__main__":
    app_settings = {
        'login_url': '/login',
        'cookie_secret': 'supersecret',
    }
    handler_settings = {
        'secret': 'jwtsecret',
        'ldap_uri': 'al-dc01.baysidehealth.intra',
        'ldap_base': 'dc=baysidehealth,dc=intra',
    }
    handlers = [
       (r'/api/([^/]*)', MainHandler, handler_settings),              
       (r'/delete/([^/]*)', DeleteHandler, handler_settings),       
       (r'/linkage/([^/]*)', LinkageHandler, handler_settings),
       (r'/login', LoginHandler, handler_settings),
       (r'/login.html', LoginHandler, handler_settings),
       (r'/index', IndexHandler, handler_settings),
       (r'/index.html', IndexHandler, handler_settings),
       (r"/(.*)", tornado.web.StaticFileHandler,{'path':r'./templates/'}),
       
       
    ]
    application = tornado.web.Application(
        handlers,        
        **app_settings)
    application.listen(8082)
    enable_pretty_logging()
    tornado.ioloop.IOLoop.current().start()

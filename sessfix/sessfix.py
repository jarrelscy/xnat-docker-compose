#!/usr/bin/env python
# RULES
# ALF_ cannot be used at the start of an accession number
# AC cannot be used at the start of an accession number
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
import os, sys, re
import datetime
import os.path as op
import shutil
import optparse
from lxml import etree
import time
import xnat
import datetime
import traceback
import json
from table_utils import *
from dicom_utils import *
from xnat.exceptions import XNATResponseError
import multiprocessing
import queue
import requests
import os
from sqlalchemy import and_, exists
import pydicom
from requests.exceptions import ReadTimeout
from contextlib import contextmanager
import datetime
import logging
from pathlib import Path
import signal
from secret import XNATPASSWORD, XNATUSER
    
XNATDBURL = 'xnat-db'
XNATURL = 'xnat-nginx'
HASHERURL = 'xnat-hasher'
    
@contextmanager
def session_scope(Session):
    """Provide a transactional scope around a series of operations."""
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()
def findSourceAE(path):
    for root, dirs, files in os.walk(path):
        for file in files:
            if file[-4:] == '.dcm':
                ds = pydicom.read_file(os.path.join(root, file))                
                if 'SourceApplicationEntityTitle' in ds.file_meta:
                    return ds.file_meta.SourceApplicationEntityTitle
    return None

MAX_NUMBER_RECEIVING = 10
NUM_POOL_WORKERS = 5
STOPFILE = '/data/xnat/sessfix.stop'
OVERRIDETIMEFILE = '/data/xnat/sessfix.override'

def exit_gracefully(signum, frame):
    if not os.path.exists(STOPFILE):
        Path(STOPFILE).touch()
            
def syncFolders(root_src_dir, root_dst_dir):
    root_src_dir=os.path.realpath(root_src_dir)
    root_dst_dir=os.path.realpath(root_dst_dir)
    for src_dir, dirs, files in os.walk(root_src_dir):
        dst_dir = src_dir.replace(root_src_dir, root_dst_dir, 1)
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)
        for file_ in files:
            src_file = os.path.join(src_dir, file_)
            dst_file = os.path.join(dst_dir, file_)
            if os.path.exists(dst_file):
                # in case of the src and dst are the same file
                if os.path.samefile(src_file, dst_file):
                    continue
                os.remove(dst_file)
            shutil.move(src_file, dst_dir)
            
def asyncArchive(obj, overwrite='append', quarantine=None, trigger_pipelines=None,
                project=None, subject=None, experiment=None, merge=False):
    query = {'src': obj.uri}

    if overwrite is not None:
        if overwrite not in ['none', 'append', 'delete']:
            raise ValueError('Overwrite should be none, append or delete!')
        query['overwrite'] = overwrite

    if quarantine is not None:
        if isinstance(quarantine, bool):
            if quarantine:
                query['quarantine'] = 'true'
            else:
                query['quarantine'] = 'false'
        else:
            raise TypeError('Quarantine should be a boolean')

    if trigger_pipelines is not None:
        if isinstance(trigger_pipelines, bool):
            if trigger_pipelines:
                query['triggerPipelines'] = 'true'
            else:
                query['triggerPipelines'] = 'false'
        else:
            raise TypeError('trigger_pipelines should be a boolean')

    # Change the destination of the session
    # BEWARE the dest argument is completely ignored, but there is a work around:
    # HACK: See https://groups.google.com/forum/#!searchin/xnat_discussion/prearchive$20archive$20service/xnat_discussion/hwx3NOdfzCk/rQ6r2lRpZjwJ
    
    if merge:
        query['dest'] = '/archive/projects/{}/subjects/{}/experiments/{}'.format(project, subject, experiment)
    else:
        if project is not None:
            query['project'] = project
        if subject is not None:
            query['subject'] = subject

        if experiment is not None:
            query['session'] = experiment
    uri = obj.xnat_session._format_uri('/data/services/archive', None, query=query)
    try:
        obj.xnat_session._interface.post(uri, timeout=1)
    except ReadTimeout as e:
        pass
        
def relabel_session_files(oldlabel, olddir, thinslice_mod=None):
   
    oldxml = olddir + '.xml'
    
    
    # source must exist and destination must not
    if not op.isdir(olddir):
        # directory is missing... maybe this has already been relabelled 
        # try to read new accession number from the xml file
        if op.isfile(oldxml):
            with open(oldxml, 'r') as f:
                accession = f.read().strip().replace('REMOVED NEW ACCESSION ', '')
                newdir = '/'.join(olddir.split('/')[:-1] + [accession])
                newxml = newdir + '.xml'
                logger.warning ('Tried to relabel prearchive session file {} found that it has already been done. new accession {}'.format(olddir, accession))
                return accession, newdir 
        else:
            raise IOError(-1, 'Missing directory or xml file in: ' + str(olddir) +  ' ' + str(op.isfile(oldxml)) + ' ' + str(op.isdir(olddir)))
    
    
    # parse xml and find the pieces to replace
    sessxml = etree.parse(oldxml)
    sessnode = sessxml.getroot()
    imagetype = ' '.join([t.text for t in sessnode.xpath(r'//xnat:imageType', namespaces={'xnat':'http://nrg.wustl.edu/xnat'})  if 'LOCALIZER' not in t.text])    
    logger.info (imagetype)
    
    source_ae = findSourceAE(olddir)
    if source_ae:
        thinslice_mod = ae_titles[source_ae][2]
    else:
        logger.warning ('Could not find which server this dicom session comes from... guessing {}'.format(oldlabel))
        if r'ORIGINAL\PRIMARY' in imagetype:
            thinslice_mod = 'ACT'
        else:
            thinslice_mod = 'ACC'
        
    accession = sessnode.xpath(r'xnat:dcmAccessionNumber', namespaces={'xnat':'http://nrg.wustl.edu/xnat'})[0].text
    if accession[:4] == 'ALF_': # means that this is a specific new accession number rather than an autogenerated hash one
        pass
    else:
        accession = thinslice_mod + accession 
        
    newdir = '/'.join(olddir.split('/')[:-1] + [accession])
    newxml = newdir + '.xml'
    
    
    if op.exists(newdir) or op.exists(newxml):
        logger.warning('Will overwrite - New dir or xml exists: {}'.format(newdir))
        
    label = sessnode.get('label')
    if not label == oldlabel:
        raise IOError('label (%s) and label in file (%s) mismatch.' % (oldlabel, label))
    
    pathnode = sessnode.xpath('//xnat:prearchivePath', namespaces=sessnode.nsmap)
    if not pathnode:
        raise IOError(-1, 'No xnat:prearchivePath in ' + oldxml)
    elif len(pathnode) > 1:
        logger.warning ("Multiple prearchivePath elements in {}".format(sessxml))
    pathnode = pathnode[0]
    if pathnode.text != olddir:
        raise IOError(-1, 'xnat:prearchivePath (%s) does not match sessdir (%s)' % (pathnode, olddir))
    
    # replace them
    sessnode.set('label', accession)
    pathnode.text = newdir
    
    pathnode = sessnode.xpath('//xnat:subject_ID', namespaces=sessnode.nsmap)[0]
    pathnode.text = sessnode.xpath('//xnat:dcmPatientId', namespaces=sessnode.nsmap)[0].text
    
    try:
        with open(newxml, 'wb') as handle:
            handle.write(etree.tostring(sessxml, encoding='UTF-8'))
        syncFolders(olddir, newdir)
        #os.remove(oldxml)
        with open(oldxml, 'w') as f:
            f.write('REMOVED NEW ACCESSION {}'.format(accession))
        shutil.rmtree(olddir)
        return accession, newdir
    except Exception as e:
        logger.warn(traceback.format_exc())
        return None, None


def main(pending_alfred_ids, session):
    
    
    
    #meta = sa.MetaData(bind=conn)
    #pa = prearchive = sa.Table('prearchive', meta, schema='xdat_search', autoload=True)    
    #subjects = sa.Table('xnat_subjectdata', meta, schema='public', autoload=True)
    #subj_cols = subjects.c.keys()
    # cols = pa.c.keys()
    #select = pa.select().where(pa.c.status == 'READY').where(pa.c.name.in_(pending_alfred_ids))
    updated_names = []
    prearchive_experiments = session.query(PrearchiveExperiment).filter(PrearchiveExperiment.status == 'READY').filter(PrearchiveExperiment.name.in_(pending_alfred_ids))
    prearchive_names = []
    for row in prearchive_experiments:
        logger.info ('Processing prearchive SQL Row: {}'.format(row.name))
        prearchive_names.append(row.name)
        if None in (row.scan_date,):
            logger.warning ("Skipping due to missing scan_date (%r) " \
                "for name / folder: %s / %s" \
                % (row.scan_date,row.name, row.foldername))
            continue
        scan_date = row.scan_date.date().isoformat()
        # already processed?
        if row.name.startswith('AC'):
            logger.info ("Already done: {}".format(row.name))
            continue 
            
        
        
        old_name = row.name
        sessdir = row.url
        
        
        try:
            new_name, newdir = relabel_session_files(old_name, sessdir)
            logger.info ('Relabelled oldname: {} sessdir: {} newname: {} newdir: {}'.format(old_name, sessdir, new_name, newdir))
            if new_name and newdir:
                
                
                updated_names.append(new_name)
                # update with new name and directory path
                # new_vals = {
                #     pa.c.name: new_name,
                #    pa.c.subject : '',
                ##    pa.c.url: newdir,
                #    pa.c.foldername: op.basename(newdir)
                #}
                #where = sa.sql.and_(*[ pa.c[col] == row[col] for col in cols ])
                #update = pa.update().values(new_vals).where(where)
                # 
                # update.execute()
                
                row.name = new_name
                row.subject = ''
                row.url = newdir
                row.foldername = op.basename(newdir)
                logger.info ('prearchive update successful {} {} {} {}'.format(old_name, sessdir, new_name, newdir))
        except Exception as e:
            logger.warn(traceback.format_exc())
                    
                
                
    session.commit()       
    
    return updated_names, prearchive_names
    
def getHash(accession, ae):    
    return (ae_titles[ae][2] if ae else '') + requests.get('http://{}:8888/'.format(HASHERURL)+accession).text
    
    
def do_xnat_stuff(sess):
    archived_names = []
    num_receiving = 0
    
    for s in sess.prearchive.sessions():
        try:
            
            if s.data['status'] == 'RECEIVING':
                num_receiving += 1
            if s.data['status'] == 'READY' and s.data['name'][:2] == 'AC':
                s.move('Alfred', asynchronous=True)
                logger.info (s.data['status'])
            elif s.data['status'] == 'READY' and s.data['name'][:4] == 'ALF_': # this will be an accession number with a specified accession - hence it cannot go into the master Alfred project and must go into its own project.
                project = s.data['name'].split('_')[1]
                s.move(project, asynchronous=True)
                logger.info (s.data['status'])
        except IndexError as e:
            logger.info("Index error, likely at name = str(s.data['status'] == 'RECEIVING' in do_xnat_stuff")
        except Exception as e:
            logger.error(traceback.format_exc())
    sess.clearcache()
    for s in sess.prearchive.sessions():
        try:
            name = str(s.data['name'])
            if (s.data['status']  == 'READY') and s.data['project'] != 'Unassigned' and (name[:2] == 'AC' or name[:4] == 'ALF_'):      
                logger.info ('Archive {} with status {}'.format(name, s.data['status']))
                asyncArchive(s)
                #s.archive(overwrite='append', project=s.data['project'], subject=subject, experiment=name)
                archived_names.append(name)
            if (s.data['status']  == 'CONFLICT') and s.data['project'] != 'Unassigned' and (name[:2] == 'AC' or name[:4] == 'ALF_'):      
                logger.info ('Archive OVERWRITE {} with status {}'.format(name, s.data['status']))
                asyncArchive(s, overwrite='delete')
                #s.archive(overwrite='append', project=s.data['project'], subject=subject, experiment=name)
                archived_names.append(name)
        except IndexError as e:
            logger.info("Index error, likely at name = str(s.data['name']) in do_xnat_stuff")
        except Exception as e:
            logger.error(traceback.format_exc())
                
    return archived_names, num_receiving
def create_xnat_project(xnatsession, name):
     xnatsession.classes.ProjectData(name=name,parent=xnatsession)
def dicom_worker(input_queue, output_queue):
    logger.info ("dicom worker {} working".format(os.getpid()))
    
    while True:
        if os.path.exists(STOPFILE):
            break
        try:
            item = input_queue.get(True, 3)
            logger.info ("{} accepted job for {}".format(os.getpid(), item))
            accession, patient_id, ae_title, reqid = item
            try:
                retrieve(accession, patient_id, ae_title)
                output_queue.put((accession, patient_id, ae_title, reqid, ''))
            except Exception as e:            
                output_queue.put((accession, patient_id, ae_title, reqid, str(e)))
        except queue.Empty:
            pass
        
            
        
def shareRequest(xnat_session, source_project, destination_project, hash, pthash):
    if source_project == destination_project: return
    subject_not_shared = True
    try:
        logger.info ("Try to share subject {} from {} into {}".format(pthash, source_project, destination_project))
        subj = xnat_session.projects[source_project].subjects[pthash]
        subj.share(destination_project, label=pthash)
        logger.info ("Successfully shared subject {} from {} into {}".format(pthash, source_project, destination_project))
        subject_not_shared = False
    except XNATResponseError as e:
        if 'Already assigned' in str(e): 
            logger.info ('Subject {} already assigned to {}'.format(pthash, destination_project))
            subject_not_shared = False # since it awas already assigned you cannot say that it is not shared, else the experiment will not be shared
        else: raise e
    except KeyError as e:
        if 'Could not find ID' in str(e): 
            logger.warning('Subject {} for experiment {} could not be found - this can happen if someone has two patient ID - or alternatively the ID may be wrong.'.format(pthash,
            hash))
        else:
            raise e
    xnat_session.clearcache()
    if not subject_not_shared: # if subject wasn't found then there'se no point in sharing the experiment
        while True: # keep trying until it succeeds
            try:        
                logger.info ("Try to share experiment {} from {} into {}".format(hash, source_project, destination_project))
                exp = xnat_session.projects[source_project].subjects[pthash].experiments[hash]
                exp.share(destination_project, label=hash)
                logger.info ("Successfully shared experiment {} from {} into {}".format(hash, source_project, destination_project))
                break
            except XNATResponseError as e:
                if 'Already assigned' in str(e): 
                    logger.info ('Experiment {} already assigned to {}'.format(hash, destination_project))
                    break
                else: 
                    raise e
            except KeyError as e:
                if 'Could not find ID' in str(e): 
                    logger.error ('Experiment {} could not be found in {}'.format(hash, source_project))
                    logger.error(e)
                    time.sleep(0.1)
                else:
                    raise e
def checkIfHashInAlfred(hash, experimentTable):
    return len(list(experimentTable.select().where(experimentTable.c.project == 'Alfred').where(experimentTable.c.label == hash).execute())) > 0

        
if __name__ == '__main__':   
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    filename='/data/xnat/home/logs/sessfix.log',
                    filemode='a')
    logger = logging.getLogger(__name__)
    # define a Handler which writes INFO messages or higher to the sys.stderr
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    # set a format which is simpler for console use
    formatter = logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)
    
    # test access
    if os.getuid() != 0:
        raise Exception('Run as sudo')
    
    # setup listener for SIGTERM and SIGINT
    signal.signal(signal.SIGINT, exit_gracefully)
    signal.signal(signal.SIGTERM, exit_gracefully)
    
    # initialize 
    engine = sa.create_engine('postgresql://xnat:xnat@{}/xnat'.format(XNATDBURL), echo=False)
    conn = engine.connect()
    # check SQL for existence of share queue that tells it which sessions go to which projects
    inspector = sa.inspect(engine)
    tablenames = inspector.get_table_names()
    # initialize ORM session
    Session = sessionmaker(bind=engine)
    
    # remove stopfile
    if os.path.exists(STOPFILE):
        os.remove(STOPFILE)
    
            
    # initialize pool
    input_queue = multiprocessing.Queue()
    output_queue = multiprocessing.Queue()
    pool = multiprocessing.Pool(NUM_POOL_WORKERS, dicom_worker,(input_queue, output_queue,))
    # initalize experiment table
    meta = sa.MetaData(bind=conn)
    experimentTable = sa.Table('xnat_experimentdata', meta, schema='public', autoload=True)

    # wait for xnat-web application start 
    while True:
        try:
            time.sleep(1)
            xnat_session = xnat.connect('http://{}'.format(XNATURL), user=XNATUSER, password=XNATPASSWORD)            
            break
        except requests.exceptions.ConnectionError as e:
            logging.info('Waiting to connect to XNAT: ' + str(e))
            
    sleepy_time = 1
    while True:
        try:
            with session_scope(Session) as session:
                # This loop does a few main things:
                # 1. look at all pending requests in the iap table and find the pending alfred IDs 
                # 2. Run the main function on each of these requests which checks if the patient ID is in the prearchive (note patient ID!). 
                #    - This function relabels the session name to the hashed accession number 
                #    - Note that the hashing occurs BY XNAT itself!!! in the anonymization script, by making a HTTP call to port 8888 where the hashing server runs
                # 3. Takes the relabelled accession numbers and checks if there are any other relabelled accession numbers that have already been sent or already exist in the Alfred XNAT project (in alfred_hashes)  This step may become a bottleneck in the future and we should definitely look into optimising this as reading all the alfred_hashes will become very long 
                # 4. Takes all hashes that are pending share.. and shares it 
                # 5. issues dicom c-move requests 
                
                time.sleep(sleepy_time)
                # logger.info ('loop')            
                xnat_session.clearcache()
                # iap_sessions_to_share accessions which are not COMPLETED or FAILED
                pending_alfred_ids = [Request.patient_id for i in session.query(Request).filter(Request.status != 'COMPLETED').filter(Request.status != 'FAILED')]            
                updated_names, prearchive_names = main(pending_alfred_ids, session)
                if len(updated_names) > 0:
                    logger.info('Updated accessions {}'.format(updated_names))
                 # logger.info ('Archive stuff')
                 
                 
                
                # CREATE PROJECTS IF REQUIRED FROM DICOMSENT                 
                existing_projects = [e.name for e in xnat_session.projects.values()]
                recently_created = []
                for i in session.query(Request).filter(Request.status == 'DICOMSENT'):
                    try:
                        if i.project not in existing_projects and i.project not in recently_created:
                            create_xnat_project(xnat_session, i.project)
                            recently_created.append(i.project)
                    except Exception as e:
                        logger.error(traceback.format_exc())  
                        
                # IMPORTANT LINE - this part moves studies to the Alfred project or a specified project and archives it from the prearchive!
                
                archived_accessions,num_receiving = do_xnat_stuff(xnat_session)        

                ###########################################
                
                
                if len(archived_accessions) > 0:
                    logger.info('Archived accessions {}'.format(archived_accessions))
                
                # logger.info ('Check existing hashes')
                # alfred_hashes = [row.label for row in select_experimentTable.execute()]
                # don't pull all alfred hashes as this is going to get really big in the future
                
                # POSSIBLE request states - PENDING, DICOMSENT, DICOMSENDING, PENDINGSHARE, COMPLETED
                # All DICOMSENT requests in iap_sessions_to_share that have been archived or are in the Alfred will now become PENDINGSHARE - so they can be shared into their respective projects
                # If SPECIFIC new_accession and new_patient_id were created then they will not go into the Alfred master project but rather into their dedicated projects directly 
                for i in session.query(Request).filter(Request.status == 'DICOMSENT'):
                    if i.new_accession or i.new_patient_id:
                        if ('ALF_'+i.project+'_'+i.new_accession) in archived_accessions:
                            i.status = 'COMPLETED'
                            i.last_updated = datetime.datetime.now()
                    else:
                        hash = getHash(i.accession, i.application_entity)
                        if checkIfHashInAlfred(hash, experimentTable) and hash not in prearchive_names:
                            i.status = 'PENDINGSHARE'
                            i.last_updated = datetime.datetime.now()
                session.commit()
                xnat_session.clearcache()
                
                for i in session.query(Request).filter(Request.status == 'PENDINGSHARE'):
                    try:
                        hash = str(getHash(i.accession, i.application_entity))
                        pthash = str(getHash(i.patient_id, ''))
                        logger.info ('Sharing Pthash {}'.format(pthash))
                        logger.info ('Sharing accession Hash {}'.format(hash))
                        shareRequest(xnat_session, 'Alfred', i.project, hash, pthash)
                        
                        i.status = 'COMPLETED'
                        i.last_updated = datetime.datetime.now()
                    except XNATResponseError as e:                    
                        if 'Could not find ID' in str(e):
                            logger.error(traceback.format_exc())                        
                        else:
                            logger.error ('Failed {}'.format(hash))
                            errtext = traceback.format_exc()
                            i.status = 'FAILED'
                            i.error = errtext
                            i.last_updated = datetime.datetime.now()
                session.commit()
                
                # logger.info ('Write output')
                # check output queue and write to SQL 
                while True:
                    try:
                        accession, patient_id, ae, request_id, error = output_queue.get(False)
                        i = session.query(Request).filter(Request.accession == accession).filter(Request.patient_id == patient_id).filter(Request.request_id == request_id).first()                        
                        if i:
                            if len(error) == 0: 
                                i.status = 'DICOMSENT'
                                i.last_updated = datetime.datetime.now()
                                sleepy_time = 1
                            elif 'Node does not have CMOVE permission' in error:
                                logger.warn('Error - Node does not have CMOVE permission')
                                i.status = 'PENDING' #put it back into the queue if this CMOVE error comes up
                                i.last_updated = datetime.datetime.now()
                                sleepy_time = 600 #wait longer for CMOVE permission for 10 minutes                              
                            elif 'Association rejected' in error:
                                logger.warn('Error - association was rejected or aborted - this might mean network connectivity issues with the PACS server')
                                i.status = 'PENDING' #put it back into the queue if this CMOVE error comes up
                                i.last_updated = datetime.datetime.now()
                                sleepy_time = 600 #wait longer for network             
                            else:
                                i.status = 'FAILED'
                                i.last_updated = datetime.datetime.now()
                                i.error = error
                    except queue.Empty:
                        break
                
                
                session.commit()
                
                if os.path.exists(STOPFILE):                    
                    raise KeyboardInterrupt
                
                hour = datetime.datetime.now().hour
                if hour >= 20 or hour < 8 or os.path.exists(OVERRIDETIMEFILE):
                    # logger.info ('Check existing hashes')
                    existing_projects = [e.name for e in xnat_session.projects.values()]   
                    # logger.info ('Finish existing hashes')
                    # check iap_sessions_to_share and send DICOM QR request
                    just_added = []            
                    for i in session.query(Request).filter(Request.status == 'PENDING').order_by(Request.last_updated):
                        found_in_alfred = False
                        if not (i.new_accession or i.new_patient_id):
                            hash = getHash(i.accession, i.application_entity)
                            if checkIfHashInAlfred(hash, experimentTable):
                                try:   
                                    found_in_alfred = True
                                    if i.project not in existing_projects:
                                        logger.warning ('Project {} not found - creating'.format(i.project))
                                        create_xnat_project(xnat_session, i.project)
                                        existing_projects = [e.name for e in xnat_session.projects.values()]
                                    pthash = str(getHash(i.patient_id, ''))
                                    logger.info('Sharing pt {} study {} with project {}'.format(pthash, hash, i.project))                                
                                    shareRequest(xnat_session, 'Alfred', i.project, hash, pthash)
                                    
                                    i.status = 'COMPLETED'
                                    i.last_updated = datetime.datetime.now()
                                    logger.info ('Already downloaded {} assigning to {}'.format(hash, i.project))
                                except XNATResponseError as e:                    
                                    errtext = traceback.format_exc()
                                    logger.error ('Failed {}'.format(hash))
                                    i.status = 'FAILED'
                                    i.error = errtext
                                    
                                    
                                    
                        if found_in_alfred:
                            pass
                        elif (i.accession,i.patient_id, i.application_entity) in just_added:
                            # has something that was just added in the same query
                            pass
                        else:
                            if (num_receiving+len(just_added)) < MAX_NUMBER_RECEIVING and input_queue.qsize() < NUM_POOL_WORKERS:
                                # submit job to multiprocessing queue 
                                logger.info ('Submit job to DICOM queue {} {} {} {}'.format(i.accession,i.patient_id, i.application_entity, i.request_id))
                                input_queue.put((i.accession,i.patient_id, i.application_entity, i.request_id))
                                i.status = 'DICOMSENDING'
                                i.last_updated = datetime.datetime.now()
                                just_added.append((i.accession,i.patient_id, i.application_entity))
                            else:
                                pass                    
                    session.commit()
        except KeyboardInterrupt:
            conn.close()
            if not os.path.exists(STOPFILE):
                Path(STOPFILE).touch()
            print ('Waiting for DICOM processes to finish...')
            pool.close()
            pool.join()
            os.remove(STOPFILE)
            exit(0)
        except:
            logger.error(traceback.format_exc())
            
        

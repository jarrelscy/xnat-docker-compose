from pydicom.dataset import Dataset
from pynetdicom import AE
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelMove
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, JPEGLossless, JPEGLSLossless, JPEG2000Lossless, ExplicitVRBigEndian
import socket
socket.setdefaulttimeout(7200)
import sys


#ae_titles = {'GEPACSD042': ('172.22.7.42', 4100, 'ACC'),
#             'GEPACSD030': ('172.22.7.30', 4100, 'ACC'),
#             'GEPACSD035': ('172.22.7.35', 4100, 'ACC'),
#             'GEPACSD036': ('172.22.7.36', 4100, 'ACC'),
#             'GEPACSD038': ('172.22.7.38', 4100, 'ACC'),
#             'GEPACSD044': ('172.22.7.44', 4100, 'ACC'),
#             'AHCTAWS': ('172.22.17.167', 4006, 'ACT'),
#             'TEST': ('172.22.2.13', 104, 'ACC'), 
#            }


def module_property(func):
    """Decorator to turn module functions into properties.
    Function names must be prefixed with an underscore."""
    module = sys.modules[func.__module__]

    def base_getattr(name):
        raise AttributeError(
            f"module '{module.__name__}' has no attribute '{name}'")

    old_getattr = getattr(module, '__getattr__', base_getattr)

    def new_getattr(name):
        if f'_{name}' == func.__name__:
            return func()
        else:
            return old_getattr(name)

    module.__getattr__ = new_getattr
    return func

@module_property
def _ae_titles(name):
    return json.load(open('/data/xnat/ae_titles.json','r'))

ts = [
JPEGLossless,
JPEGLSLossless,
JPEG2000Lossless,
ExplicitVRLittleEndian,
]
            
from pydicom.dataset import Dataset
from pynetdicom import AE
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, PatientRootQueryRetrieveInformationModelMove, StudyRootQueryRetrieveInformationModelMove
import traceback
import logging
logger = logging.getLogger('dicom_utils')

def cfind(ds, assoc, ):
    if assoc.is_established:
        responses = assoc.send_c_find(ds, query_model='S')
        valid_ids = []
        for (status, identifier) in responses:
            if 'Status' not in status:
                logger.debug('C-FIND: {}'.format(status))
            else:
                if status.Status != 0:
                    logger.debug('C-FIND: {}'.format(status))
                logger.debug('C-FIND query status: 0x{0:04x}'.format(status.Status))
                if status.Status in (0xFF00, 0xFF01):
                    valid_ids.append(identifier)
        if len(valid_ids) == 0:
            raise Exception('Not found')
        return valid_ids
    else:
        raise Exception('Association rejected or aborted')

def cmove(ds, assoc, ):
    if assoc.is_established:
        responses = assoc.send_c_move(ds, b'RESEARCH', query_model='S')
        for (status, identifier) in responses:
            if 'Status' not in status:
                raise Exception(str(status))
            if status.Status != 0:
                logger.debug('C-MOVE: {}'.format(str(responses)))
            logger.debug('C-MOVE query status: 0x{0:04x} Progress: {1}/{2}'.format(status.Status, 
                status.NumberOfCompletedSuboperations if 'NumberOfCompletedSuboperations' in status else '-', 
                ((status.NumberOfRemainingSuboperations if 'NumberOfRemainingSuboperations' in status else 0)
                    +status.NumberOfCompletedSuboperations) if 'NumberOfCompletedSuboperations' in status else '-'))
                
            if status.Status == 0xc001: 
                raise Exception(str(status))        
    else:
        raise Exception('Association rejected or aborted')

        
def retrieve(accession, patient_id, ae_title = 'GEPACSD042', ):
    ae = AE('RESEARCH')
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove, transfer_syntax=ts)
    assoc = ae.associate(__getattr__('ae_titles')[ae_title][0], __getattr__('ae_titles')[ae_title][1], ae_title=ae_title)
    logger.debug(str(assoc))
    try:
        # get study id
        ds = Dataset()
        ds.AccessionNumber = accession
        ds.PatientID = patient_id
        ds.QueryRetrieveLevel = 'STUDY'
        ds.StudyInstanceUID = ''
        StudyInstanceUID = [i.StudyInstanceUID for i in cfind(ds, assoc)][0]    
        identifiers = [StudyInstanceUID]
        ds.StudyInstanceUID = StudyInstanceUID
        cmove(ds, assoc)

        # get series id
        #ds.QueryRetrieveLevel = 'SERIES'
        #ds.StudyInstanceUID = StudyInstanceUID
        #ds.SeriesInstanceUID = ''
        #ds.SeriesDescription = ''
        #ds.ImageType = ''
        #ds.KVP = ''
        #identifiers = cfind(ds, assoc, )
        #logger.debug ('Num series found: {}'.format(len(identifiers)))
        #logger.debug (identifiers)
        
        # move series
        #for id in identifiers:
        #    ds.SeriesInstanceUID = id.SeriesInstanceUID
        #    cmove(ds, assoc, )
            
        assoc.release()
    except Exception as e:
        assoc.release()    
        raise e
    assert len(identifiers) > 0, "No studies found for {} from {}".format(accession, ae_title)
    return identifiers
        

    

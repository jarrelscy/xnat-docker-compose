from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Integer, Column, String, DateTime, TIMESTAMP, Boolean
Base = declarative_base()
class Request(Base):
    __tablename__ = 'iap_sessions_to_share'
    patient_id = Column(String, primary_key=True)
    accession = Column(String, primary_key=True)
    application_entity = Column(String)
    project = Column(String)
    status = Column(String)
    request_id = Column(String, primary_key=True)
    last_updated = Column(DateTime)
    error = Column(String)
    created = Column(DateTime)
    new_patient_id = Column(String)
    new_accession = Column(String)
    
class PrearchiveExperiment(Base):
    __tablename__ = 'prearchive'
    __table_args__ = ({"schema": "xdat_search"})
    project = Column(String)
    timestamp = Column(String, primary_key=True)
    lastmod = Column(TIMESTAMP)
    uploaded = Column(TIMESTAMP)
    scan_date = Column(TIMESTAMP)
    scan_time = Column(String)
    subject = Column(String)
    foldername = Column(String, primary_key=True)
    name = Column(String)
    tag = Column(String)
    status = Column(String)
    url = Column(String)
    autoarchive = Column(String)
    prevent_anon = Column(Boolean)
    prevent_auto_commit = Column(Boolean)
    source = Column(String)
    visit = Column(String)
    protocol = Column(String)
    timezone = Column(String)
    
    
    
if __name__ == '__main__':
    pass


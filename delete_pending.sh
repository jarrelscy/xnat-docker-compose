docker exec -it `docker ps | grep xnat-db | cut -c1-10` psql -U xnat -d xnat -c "DELETE FROM iap_sessions_to_share WHERE STATUS='PENDING' AND project='ALF19-131' "

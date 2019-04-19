docker exec -it `docker ps | grep xnat-db | cut -c1-10` psql -U xnat -d xnat -c 'REINDEX (VERBOSE) DATABASE xnat'

docker stop -t 10 `docker ps | grep xnat-sessfix | cut -c1-10`
docker-compose down -t 30


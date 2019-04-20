#!/bin/bash
TIME=`date +%b-%d-%y`
FILENAME=postgres-data-back-$TIME.tar.gz
SRCDIR=/home/jarrels/xnat-docker-compose/postgres-data
DESDIR=/data/shared/temp/db-backups
tar -cpzf $DESDIR/$FILENAME $SRCDIR

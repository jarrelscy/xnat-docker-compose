#!/bin/bash
cd ./xsync
./gradlew clean fatjar
cd ..
sudo cp ./xsync/build/libs/xsync-plugin-all-1.3.3.jar /data/xnat/home/plugins/xsync-plugin-all-1.3.3.jar


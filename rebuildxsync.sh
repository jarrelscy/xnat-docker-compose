#!/bin/bash
cd ~/xsync
./gradlew clean fatjar
sudo cp ./build/libs/xsync-plugin-all-1.3.3.jar /data/xnat/home/plugins/xsync-plugin-all-1.3.3.jar


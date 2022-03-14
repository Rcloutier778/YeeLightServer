#!/bin/bash

curl -X POST -H "Content-Type: application/json" -d '{"newState": "sunrise", "eventType": "dashboard"}' --max-time 60 http://10.0.0.18:9001

# Old python way
#DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
#sudo /usr/local/bin/python3.7 $DIR/yeelightpython.py sunrise_http

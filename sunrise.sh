#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
sudo /usr/local/bin/python3.7 $DIR/yeelightpython.py sunrise_http

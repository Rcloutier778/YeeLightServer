
import requests, json
import platform


data = {"eventType": "manual", "user": 'richard'}
params = {}

requests.post('http://10.0.0.17:9000',params=params, json=data)

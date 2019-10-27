import json
import os

with open('bulbStateLog','w+') as f:
    json.dump({'state':'day','pcStatus':True,'phoneStatus':True},f)


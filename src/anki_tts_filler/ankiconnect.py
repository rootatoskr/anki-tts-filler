import json
import urllib.request
from .config import ANKICONNECT_URL


def invoke(action, **params):
    payload = json.dumps({'action': action, 'version': 6, 'params': params}).encode('utf-8')
    req = urllib.request.Request(ANKICONNECT_URL, data=payload)
    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode('utf-8'))
    if result['error'] is not None:
        raise Exception(result['error'])
    return result['result']

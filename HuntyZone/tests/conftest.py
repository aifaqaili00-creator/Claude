import os

os.environ.setdefault('PC_FX_OFFLINE', '1')         # tests never fetch exchange rates from the internet
os.environ.pop('PC_NO_EMAIL', None)                 # tests replace the browser and the mail server themselves

import os

# The suite runs against SQLite with a throwaway signing key; production refuses weak secrets.
os.environ.setdefault("HCM_ALLOW_INSECURE_SECRET", "1")
os.environ.setdefault("LOGIN_ATTEMPTS_PER_IP", "100000")  # every test logs in from the same client address

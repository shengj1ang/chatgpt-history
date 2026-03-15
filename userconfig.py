from pathlib import Path


DB_PATH = Path("history.db")
SOURCE = Path("exports")
PORT = 8000
debug = False

AUTH_ENABLED = True
AUTH_USERNAME = "admin"
AUTH_PASSWORD = "password"
SECRET_KEY = "change_this_to_a_random_secret"

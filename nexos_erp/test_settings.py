"""
Test settings — Uses SQLite in-memory for fast test execution.
"""
from .settings import *  # noqa: F401, F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Speed up password hashing for tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

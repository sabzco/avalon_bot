import os

try:
    from .local_env import env
except ImportError:
    env = {}
env.update(os.environ)


def as_boolean(val) -> bool:
    return str(val).strip().lower() not in ['', '0', 'false', 'none']


REDIS_URL = env.get('REDIS_URL', 'redis://127.0.0.1:6379/0')
BOT_TOKEN = env.get('BOT_TOKEN')
BOT_PROXY = env.get('BOT_PROXY')
GAME_DEBUG = as_boolean(env.get('GAME_DEBUG'))

REDIS_PREFIX_GAME = 'game_'
REDIS_PREFIX_GAME_HISTORY = 'history_game_'
REDIS_PREFIX_LISTENER = 'listener_'
GAME_RETENTION = 7 * 24 * 3600  # 7days
REDIS_PREFIX_GAME_LOCK = 'lock_game_'
REDIS_PREFIX_LISTENER_LOCK = 'lock_listener_'

SSH_HOST_KEY = env.get('SSH_HOST_KEY')

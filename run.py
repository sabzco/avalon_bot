import asyncio
import logging
import warnings

warnings.filterwarnings(
    action='ignore',
    category=UserWarning,
    message="Blowfish|SEED|CAST5 has been deprecated",
)

from avalon_bot.bot import main
from avalon_ssh.server import start_server

loop = asyncio.get_event_loop()
loop.run_until_complete(start_server())

logging.getLogger('telegram').setLevel(logging.INFO)
logging.getLogger('httpx').setLevel(logging.INFO)
logging.getLogger('asyncssh').setLevel(logging.WARNING)
main()

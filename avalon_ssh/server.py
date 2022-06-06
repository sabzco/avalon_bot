import asyncio
import hashlib
import logging
import os
import sys

import asyncssh
from asyncssh import SSHKey, SSHServerConnectionOptions
from asyncssh.misc import MaybeAwait
from asyncssh.server import _NewSession

from avalon import config
from avalon_ssh.handler import SshGameHandler

logger = logging.getLogger(__name__)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.DEBUG)


async def handle_client(process: asyncssh.SSHServerProcess):
    # noinspection PyBroadException
    try:
        user_identity = hashlib.md5(process.get_extra_info('key_data')).hexdigest()[:16]
        handler = SshGameHandler(process, user_identity)
        await handler.handle_connection()
    except asyncssh.BreakReceived:
        process.stdout.write('\n Bye!\n')
        process.exit(0)
    except:
        logger.exception('Unhandled exception')
        process.exit(1)


class MySSHServerProcess(asyncssh.SSHServerProcess):
    def terminal_size_changed(self, *a_args, **_kwargs) -> None:
        pass


class MySSHServerConnection(asyncssh.SSHServerConnection):
    async def validate_public_key(self, username: str, key_data: bytes, msg: bytes, signature: bytes) -> bool:
        self.set_extra_info(key_data=key_data)
        return True


class MySSHServer(asyncssh.SSHServer):
    def public_key_auth_supported(self) -> bool:
        return True

    def session_requested(self) -> MaybeAwait[_NewSession]:
        # noinspection PyTypeChecker
        return MySSHServerProcess(process_factory=handle_client,
                                  sftp_factory=None,
                                  sftp_version=asyncssh.sftp.MIN_SFTP_VERSION,
                                  allow_scp=False)

    def validate_public_key(self, username: str, key: SSHKey) -> MaybeAwait[bool]:
        return True


async def start_server():
    loop = asyncio.get_event_loop()

    # await asyncssh.create_server(MySSHServer, '', 8022, server_host_keys=[config.SSH_HOST_KEY], reuse_port=True)
    def conn_factory() -> asyncssh.SSHServerConnection:
        """Return an SSH client connection factory"""
        return MySSHServerConnection(loop, options)

    options = SSHServerConnectionOptions(server_host_keys=[config.SSH_HOST_KEY], server_factory=MySSHServer)
    await loop.create_server(conn_factory, host='', port=8022, reuse_port=True)


os.environ['FORCE_COLOR'] = '2'


def main():
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(start_server())
    except (OSError, asyncssh.Error) as exc:
        sys.exit('Error starting server: ' + str(exc))

    loop.run_forever()


if __name__ == '__main__':
    main()

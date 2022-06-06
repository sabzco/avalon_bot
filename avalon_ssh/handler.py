import asyncio
import re
import sys
from typing import Optional

from asyncssh import SSHServerProcess
from colored import fg, attr

from avalon.exceptions import InvalidActionException
from avalon.game import EventListener, Game, GamePhase, GameEvent, VotingCompleted, \
    QuestFailedByTooManyRejections, FAIL_EMOJI, QuestCompleted, GamePhaseChanged, GameDeleted
from avalon_ssh.ssh_game import SshParticipant, SshListener


class SshGameHandler:
    def __init__(self, process: SSHServerProcess, user_identity: str):
        self.process = process
        self.stdout = process.stdout
        self.new_actor = SshParticipant(process.get_extra_info('username'), user_identity)
        self.listener: Optional[SshListener] = None
        self.user_identity = user_identity
        self.current_input: Optional[asyncio.Task] = None
        self.listen_task: Optional[asyncio.Task] = None
        self.last_printed_step = ''
        self.cursor = f'{self.new_actor.username}> '

    async def process_command(self, command):
        if command == '/help':
            msg = '/my-info    Show your info (while playing).\n'
            msg += '/restart    Restart game (probably with same persons).\n'
            msg += '/game-info  Print the game info\n'
            msg += '/delete     Stop and remove the current game.\n'
            self.stdout.write(msg)
            return
        listener = await EventListener.load_by_id(self.user_identity)
        if not listener:
            self.stdout.write('No game found\n')
            return

        if command == '/delete':
            await listener.game.delete()
        elif command == '/restart':
            listener.game.restart()
            await listener.game.save()
        elif command == '/my-info':
            self.stdout.write(listener.game.get_user_info(self.actor) + '\n')
        elif command == '/game-info':
            self.stdout.write(self.game_info())
        else:
            self.stdout.write('Invalid command\n')

    async def read_input(self, *values, regex=None, to_lower=True, prompt=None, msg='Invalid input'):
        if prompt is not None:
            self.stdout.write(f"{prompt}\n{self.cursor}")
        while True:
            data = (await self.process.stdin.readuntil('\n')).strip()
            self.stdout.write(data + '\n')
            if to_lower:
                data = data.lower()
            if data.startswith('/'):
                await self.process_command(data)
                self.stdout.write(self.cursor)
                continue
            if regex and re.match(regex + r'\Z', data):
                return data
            if values and data in values:
                return data
            if data:
                self.stdout.write(f"{fg('red')}{msg}{attr(0)}\n{self.cursor}")
            else:
                self.stdout.write(self.cursor)

    async def listen_for_changes(self):
        listener = self.listener
        try:
            async with listener.listen():
                while True:
                    event: GameEvent = await listener.queue.get()
                    await listener.reload_game()
                    if isinstance(event, GameDeleted):
                        self.current_input.cancel()
                        break
                    if isinstance(event, VotingCompleted):
                        msg = listener.get_voting_result_message(event.result)
                    elif isinstance(event, QuestFailedByTooManyRejections):
                        msg = f'QUEST FAILED {FAIL_EMOJI} Too many rejections'
                    elif isinstance(event, QuestCompleted):
                        msg = listener.get_quest_result_message(event.result, event.failed_votes, event.success_votes)
                    elif isinstance(event, GamePhaseChanged):
                        msg = self.game_info()
                    else:
                        self.current_input.cancel()
                        # VotesChanged, GameParticipantsChanged, QuestTeamChanged, QuestActionsChanged, GamePhaseChanged
                        msg = listener.get_current_phase_message()
                    if msg != self.last_printed_step:
                        self.current_input.cancel()
                        self.stdout.write('\n' + msg)
                        self.last_printed_step = msg
        finally:
            self.listener = None

    def game_info(self):
        return '---------------\nCurrent Game State:\n' + \
               self.listener.get_game_start_message() + \
               '---------------\n'

    async def handle_connection(self):
        self.stdout.write(f"Welcome to Avalon Bot, {self.new_actor}!\n\n")

        while True:
            listener = await EventListener.load_by_id(self.user_identity)
            if not listener:
                self.stdout.write('Please choose an option:\n  1) Create a new game\n  2) Join an existing game\n'
                                  + self.cursor)
                response = await self.read_input('1', '2')
                if response == '1':  # new game
                    game = Game(participants=[self.new_actor])
                    await game.save()
                    listener = SshListener(self.user_identity, game)
                if response == '2':  # join a game
                    self.stdout.write('Enter join key (e.g 123-456), or (B)ack\n' + self.cursor)
                    while True:
                        response = await self.read_input(regex=r'[\w-]+')
                        if response == 'b':
                            break
                        game = await Game.load_by_id(response)
                        if game:
                            listener = SshListener(self.user_identity, game)
                            break
                    if response == 'b':
                        continue
                await listener.save()
            self.listener = listener
            self.listen_task = asyncio.create_task(self.listen_for_changes())
            while self.listener:
                try:
                    await self.handle_game()
                except InvalidActionException as e:
                    self.stdout.write(f"{fg('red')}{e}{attr(0)}\n")
                except asyncio.CancelledError:
                    pass

    @property
    def actor(self):
        return self.listener.game.get_participant_by_id(self.user_identity)

    async def handle_game(self):
        listener = self.listener
        game = listener.game
        msg = listener.get_current_phase_message()
        if msg != self.last_printed_step:
            self.stdout.write(msg)
            self.last_printed_step = msg
        inp = self.read_input(prompt='')  # wait forever
        if game.phase == GamePhase.Joining:
            inp = self.read_input('j', 'l', 'p', prompt='(J)Join (L)Leave (P)Play')
        if game.phase == GamePhase.Started:
            inp = self.read_input('p', prompt='(P)Play')
        if game.phase == GamePhase.TeamBuilding:
            if self.actor == game.king:
                prompt = f'Comma separated 1-{len(game.participants)} to toggle team, then (C)Confirm'
                inp = self.read_input(prompt=prompt, regex='c|[0-9,]*')
        if game.phase == GamePhase.TeamVote:
            inp = self.read_input('a', 'r', prompt='(A)Approve (R)Reject')
        if game.phase == GamePhase.Quest:
            if self.actor in game.current_team:
                inp = self.read_input('s', 'f', prompt='(S)success (F)Fail')
        if game.phase == GamePhase.Lady:
            if self.actor == game.lady:
                inp = self.read_input(prompt=f'1-{len(game.next_lady_candidates())} to select next lady',
                                      regex=f'[1-{len(game.next_lady_candidates())}]')
        if game.phase == GamePhase.GuessMerlin:
            if self.actor == game.get_assassin():
                inp = self.read_input(prompt=f'1-{len(game.merlin_candidates())} to select merlin',
                                      regex=f'[1-{len(game.merlin_candidates())}]')

        # ------------------
        self.current_input = asyncio.create_task(inp)
        last_save = game.last_save
        response = await self.current_input

        async with Game.lock(game.game_id):
            game = await listener.reload_game()
            if game.last_save != last_save:
                self.stdout.write(f"{fg('red')}Game has been changed out of this context, Please Retry.{attr(0)}\n")
                return

            if game.phase == GamePhase.Joining:
                if response == 'j':
                    game.add_participant(self.new_actor)
                elif response == 'l':
                    game.remove_participant(self.new_actor)
                else:
                    game.play()
                await game.save()
            elif game.phase == GamePhase.Started:
                game.proceed_to_game()
                await game.save()
            elif game.phase == GamePhase.TeamBuilding:
                if response == 'c':
                    game.confirm_team(self.actor)
                else:
                    for num in response.split(','):
                        if not num or int(num) < 1 or int(num) > len(game.participants):
                            raise InvalidActionException('Invalid participant number: ' + num)
                        game.select_for_team(self.actor, game.participants[int(num) - 1].identity)
                await game.save()
            elif game.phase == GamePhase.TeamVote:
                game.vote(self.actor, response == 'a')
                game.process_vote_results()
                await game.save()
            elif game.phase == GamePhase.Quest:
                game.quest_action(self.actor, response == 's')
                game.process_quest_result()
                await game.save()
            elif game.phase == GamePhase.Lady:
                p = game.set_next_lady(self.actor, game.next_lady_candidates()[int(response) - 1].identity)
                # TODO: add /lady to retry passing this message
                self.stdout.write(f'{p} is {"" if p.role.is_evil else "NOT "}an evil\n')
                await game.save()
            elif game.phase == GamePhase.GuessMerlin:
                game.guess_merlin(self.actor, game.merlin_candidates()[int(response) - 1].identity)
                await game.save()

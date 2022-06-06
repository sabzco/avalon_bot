import asyncio
import enum
import pickle
import random
import re
import weakref
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from random import sample
from typing import Optional

import aioredis

from avalon import config, exceptions
from avalon.exceptions import InvalidActionException, OnlyKingCanDo, OnlyLadyCanDo, InvalidParticipant, \
    OnlyAssassinCanDo

redis_client = aioredis.from_url(config.REDIS_URL)
SUCCESS_EMOJI = "🏆"
FAIL_EMOJI = "🏴‍☠️"
KING_EMOJI = "👑"
LADY_EMOJI = "👱‍♀️"


def verify_identity(identity):
    if not isinstance(identity, str) or not re.match(r'^[\w-]{0,64}\Z', identity):
        raise ValueError('Invalid identity: ' + str(identity))


class Participant:
    def __init__(self, identity: str):
        verify_identity(identity)
        self.identity = identity
        self.role: Optional[Role] = None
        self.vote: Optional[bool] = None
        self.quest_action: Optional[bool] = None

    @property
    def current_vote_text(self):
        if self.vote is None:
            return 'Not voted'
        return 'Approved' if self.vote else 'Rejected'

    @property
    def current_quest_action_text(self):
        if self.vote is None:
            return 'Nothing'
        return 'Success' if self.quest_action else 'Fail'

    def __eq__(self, other):
        return isinstance(other, Participant) and self.identity == other.identity

    def __str__(self):
        return self.identity


class GamePhase(enum.Enum):
    Joining = 'Joining'
    Started = 'Started'
    TeamBuilding = 'TeamBuilding'
    TeamVote = 'TeamVote'
    Quest = 'Quest'
    Lady = 'Lady'
    GuessMerlin = 'GuessMerlin'
    Finished = 'Finished'


class Role(enum.Enum):
    Merlin = 'Merlin'
    Percival = 'Percival'
    Servant = 'Servant'
    Mordred = 'Mordred'
    Assassin = 'Assassin'
    Morgana = 'Morgana'
    Minion = 'Minion'
    Oberon = 'Oberon'

    @property
    def is_evil(self):
        return self not in SERVANT_ROLES

    @property
    def emoji(self):
        return ROLE_EMOJI[self]


SERVANT_ROLES = [Role.Merlin, Role.Servant, Role.Percival]
MERLIN_INFO = [Role.Minion, Role.Morgana, Role.Assassin]
PERCIVAL_INFO = [Role.Merlin, Role.Morgana]
EVIL_INFO = [Role.Minion, Role.Morgana, Role.Assassin, Role.Mordred]
ROLE_EMOJI = {
    Role.Merlin: '🎅🏻',
    Role.Percival: '🏇',
    Role.Servant: '🤵',
    Role.Mordred: '🎩',
    Role.Assassin: '☠️',
    Role.Morgana: '🦹‍♀️',
    Role.Minion: '💀',
    Role.Oberon: '👹',
}


class GamePlan:
    def __init__(self, steps, roles, lady_step=2):
        self.roles = [Role[r] for r in roles.split(',')]
        # noinspection PyTypeChecker
        self.steps: list[tuple[int, int]] = [list(map(int, st.split('/'))) for st in steps.split()]
        assert len(self.steps) == 5, steps
        self.lady_step = lady_step
        assert Role.Percival not in self.roles or (Role.Merlin in self.roles and Role.Morgana in self.roles)


GAME_PLANS = {
    # 5: GamePlan('1/2,1/3,1/2,1/3,1/3', 'Servant,Percival,Merlin,Assassin,Morgana'),
    5: GamePlan('1/2 1/3 1/2 1/3 1/3', 'Servant,Servant,Merlin,Assassin,Mordred'),
    6: GamePlan('1/2 1/3 1/4 1/3 1/4', 'Servant,Servant,Percival,Merlin,Assassin,Morgana'),
    7: GamePlan('1/2 1/3 1/3 2/4 1/4', 'Servant,Servant,Servant,Merlin,Assassin,Minion,Minion'),
    8: GamePlan('1/3 1/4 1/4 2/5 1/5', 'Servant,Servant,Servant,Percival,Merlin,Assassin,Morgana,Mordred'),
    9: GamePlan('1/3 1/4 1/4 2/5 1/5', 'Servant,Servant,Servant,Servant,Percival,Merlin,Assassin,Morgana,Mordred'),
    10: GamePlan('1/3 1/4 1/4 2/5 1/5',
                 'Servant,Servant,Servant,Servant,Percival,Merlin,Assassin,Morgana,Minion,Oberon'),
}
if config.GAME_DEBUG:
    GAME_PLANS[2] = GamePlan('1/1 1/1 1/1 1/1 1/1', 'Merlin,Assassin')
    GAME_PLANS[3] = GamePlan('1/1 1/1 1/1 1/1 1/1', 'Servant,Merlin,Assassin')


class Game:
    def __init__(self, game_id='', participants: Optional[list[Participant]] = None,
                 _last_phase=GamePhase.Joining):
        verify_identity(game_id)
        if not game_id:
            game_id = '{}-{}'.format(random.randint(100, 999), random.randint(100, 999))
        self.created = self.last_save = datetime.utcnow()
        self.game_result: Optional[bool] = None  # True: servant-won, False: evil-won
        self.failed_voting_count = 0
        self.game_id = game_id
        self.participants: list[Participant] = participants or []
        self.current_team: list[Participant] = []
        self.phase = GamePhase.Joining
        self._last_phase = _last_phase
        self.round_result: list[bool] = []  # True: servant-won, False: evil-won
        self.king: Optional[Participant] = None
        self.lady: Optional[Participant] = None
        self.past_ladies: list[Participant] = []

    def add_participant(self, participant: Participant):
        self.require_game_phase(GamePhase.Joining)
        try:
            self.get_participant_by_id(participant.identity)
            raise exceptions.AlreadyJoined
        except InvalidParticipant:
            self.participants.append(participant)
            self.publish_event(GameParticipantsChanged())

    def remove_participant(self, participant: Participant):
        self.require_game_phase(GamePhase.Joining)
        try:
            self.get_participant_by_id(participant.identity)
        except InvalidParticipant:
            raise exceptions.NotJoined from None
        self.participants = [p for p in self.participants if p.identity != participant.identity]
        self.publish_event(GameParticipantsChanged())

    @property
    def plan(self) -> GamePlan:
        return GAME_PLANS[len(self.participants)]

    @property
    def step(self) -> tuple[int, int]:
        return self.plan.steps[len(self.round_result)]

    def play(self):
        self.require_game_phase(GamePhase.Joining)
        if len(self.participants) not in GAME_PLANS:
            raise InvalidActionException('Game should have 5 to 10 participants')
        for role, p in zip(sample(self.plan.roles, len(self.participants)), self.participants):
            p.role = role
        self.king, self.lady = sample(self.participants, 2)
        self.phase = GamePhase.Started

    def get_user_info(self, pr: Participant):
        msg = f'You role: {pr.role.value}'
        if pr.role == Role.Merlin:
            msg += ', Evil: {}'.format(', '.join(str(p) for p in self.participants if p.role in MERLIN_INFO))
        if pr.role == Role.Percival:
            msg += ', Morgana/Merlin: {}'.format(
                ', '.join(str(p) for p in self.participants if p.role in PERCIVAL_INFO))
        if pr.role.is_evil:
            msg += ', Teammates: {}'.format(
                ', '.join(str(p) for p in self.participants if p.role in EVIL_INFO and pr != p))
        return msg

    def proceed_to_game(self):
        self.require_game_phase(GamePhase.Started)
        self.phase = GamePhase.TeamBuilding

    def select_for_team(self, participant: Participant, identity: str):
        self.require_game_phase(GamePhase.TeamBuilding)
        if self.king != participant:
            raise OnlyKingCanDo
        p = self.get_participant_by_id(identity)
        if p in self.current_team:
            self.current_team.remove(p)
        else:
            self.current_team.append(p)
        self.publish_event(QuestTeamChanged())

    def confirm_team(self, participant: Participant):
        self.require_game_phase(GamePhase.TeamBuilding)
        if self.king != participant:
            raise OnlyKingCanDo
        if len(self.current_team) != self.step[1]:
            raise InvalidActionException('Please select correct number of team members')
        self.phase = GamePhase.TeamVote
        for p in self.participants:
            p.vote = None
        participant.vote = True

    def vote(self, participant: Participant, vote: bool):
        self.require_game_phase(GamePhase.TeamVote)
        old_is_none = participant.vote is None
        participant.vote = None if (participant.vote is vote) else vote
        visible = old_is_none != (participant.vote is None)
        self.publish_event(VotesChanged(visible))

    def process_vote_results(self) -> Optional[bool]:
        """
        Move to next phase if all votes are casted.
        The next phase is one of TeamBuilding, Quest or Finished
        :return: voting_result (bool) or None if voting is not completed
        """
        self.require_game_phase(GamePhase.TeamVote)
        if not all(p.vote is not None for p in self.participants):  # all-voted
            return
        is_voting_succeeded = sum(p.vote for p in self.participants) > (len(self.participants) / 2)
        self.publish_event(VotingCompleted(is_voting_succeeded))
        if is_voting_succeeded:
            self.start_quest()
            self.failed_voting_count = 0
            return True
        self.failed_voting_count = getattr(self, 'failed_voting_count', 0) + 1
        if self.failed_voting_count >= len(self.participants):
            self.round_result.append(False)
            self.failed_voting_count = 0
            self.publish_event(QuestFailedByTooManyRejections())
            if sum(not res for res in self.round_result) == 3:  # evil won
                self.finish(False)
            else:
                self.move_to_next_team_building()
        else:
            self.move_to_next_team_building()
        return False

    def move_to_next_team_building(self):
        self.phase = GamePhase.TeamBuilding
        self.current_team = []
        ps = self.participants
        self.king = ps[(ps.index(self.king) + 1) % len(ps)]

    def start_quest(self):
        self.phase = GamePhase.Quest
        for p in self.participants:
            p.quest_action = None

    def quest_action(self, participant: Participant, success: bool):
        self.require_game_phase(GamePhase.Quest)
        if participant not in self.current_team:
            raise InvalidActionException('You are not a member of this quest')
        participant.quest_action = None if (participant.quest_action is success) else success
        self.publish_event(QuestActionsChanged())

    def process_quest_result(self) -> Optional[tuple[bool, int]]:
        """
        Move to next phase if all quest actions are casted.
        The next phase is one of TeamBuilding, Lady, GuessMerlin or Finished
        :return: None if quest is not completed
                is_quest_succeeded (bool), and number failed votes (int)
        """
        self.require_game_phase(GamePhase.Quest)
        if not all(p.quest_action is not None for p in self.current_team):  # all-voted
            return
        failed_votes = sum(not p.quest_action for p in self.current_team)
        is_quest_succeeded = failed_votes < self.step[0]
        self.round_result.append(is_quest_succeeded)
        self.publish_event(QuestCompleted(is_quest_succeeded, failed_votes, len(self.current_team) - failed_votes))
        if sum(not res for res in self.round_result) == 3:  # evil won
            self.finish(False)
        elif sum(res for res in self.round_result) == 3:  # servant won
            self.phase = GamePhase.GuessMerlin
        elif len(self.round_result) >= self.plan.lady_step and self.next_lady_candidates():
            self.phase = self.phase.Lady
        else:
            self.move_to_next_team_building()
        return is_quest_succeeded, failed_votes

    def next_lady_candidates(self):
        return [p for p in self.participants if p != self.lady and p not in self.past_ladies]

    def merlin_candidates(self):
        return [p for p in self.participants if not p.role.is_evil]

    def set_next_lady(self, participant: Participant, next_identity: str, dry_run=False) -> Participant:
        self.require_game_phase(GamePhase.Lady)
        if participant != self.lady:
            raise OnlyLadyCanDo
        next_lady = self.get_participant_by_id(next_identity)
        if next_lady not in self.next_lady_candidates():
            raise InvalidActionException('Cannot pass lady to: ' + str(next_lady))
        if not dry_run:
            self.past_ladies.append(self.lady)
            self.lady = next_lady
            self.move_to_next_team_building()
        return next_lady

    def guess_merlin(self, participant: Participant, identity: str, dry_run=False) -> Participant:
        self.require_game_phase(GamePhase.GuessMerlin)
        if participant != self.get_assassin():
            raise OnlyAssassinCanDo
        p = self.get_participant_by_id(identity)
        if p.role.is_evil:
            raise InvalidActionException('Evils cannot be merlin!')
        if not dry_run:
            self.finish(p.role is not Role.Merlin)
        return p

    def get_assassin(self):
        for p in self.participants:
            if p.role is Role.Assassin:
                return p
        for p in self.participants:
            if p.role.is_evil:
                return p

    def require_game_phase(self, phase: GamePhase):
        if self.phase != phase:
            raise exceptions.InvalidActionInThisPhase

    def finish(self, servant_won):
        self.phase = GamePhase.Finished
        self.game_result = servant_won

    @staticmethod
    def lock(game_id):
        return redis_client.lock(config.REDIS_PREFIX_GAME_LOCK + game_id, timeout=120)

    def restart(self):
        self.__init__(self.game_id, participants=self.participants, _last_phase=self.phase)

    def publish_event(self, event: 'GameEvent'):
        if not hasattr(self, '_pending_events'):
            # noinspection PyAttributeOutsideInit
            self._pending_events = []
        # Keep only one event of each type
        for ev in self._pending_events:
            if isinstance(ev, type(event)):
                return
        self._pending_events.append(event)

    async def save(self):
        old_pickle = getattr(self, '_old_pickle', None)
        if old_pickle:
            # noinspection PyUnresolvedReferences
            del self._old_pickle
            if pickle.dumps(self) == old_pickle:
                return

        self.last_save = datetime.utcnow()
        if self._last_phase != self.phase:
            self.publish_event(GamePhaseChanged())
        self._last_phase = self.phase
        pending_events = getattr(self, '_pending_events', ())
        if isinstance(pending_events, list):
            del self._pending_events
        await redis_client.setex(config.REDIS_PREFIX_GAME + self.game_id, config.GAME_RETENTION, pickle.dumps(self))
        for event in pending_events:
            InMemoryPubSub.publish(self, event)

    @classmethod
    async def load_by_id(cls, game_id: str) -> 'Game':
        value = await redis_client.get(config.REDIS_PREFIX_GAME + game_id)
        if value:
            game = pickle.loads(value)
            game._old_pickle = value
            return game

    async def delete(self):
        await redis_client.delete(config.REDIS_PREFIX_GAME + self.game_id)
        InMemoryPubSub.publish(self, GameDeleted())

    def get_participant_by_id(self, identity):
        for p in self.participants:
            if p.identity == identity:
                return p
        raise InvalidParticipant


class GameEvent:
    pass


class GamePhaseChanged(GameEvent):
    pass


class GameParticipantsChanged(GameEvent):
    pass


class QuestTeamChanged(GameEvent):
    pass


class VotesChanged(GameEvent):
    def __init__(self, is_visible=False):
        self.is_visible = is_visible


class VotingCompleted(GameEvent):
    def __init__(self, result: bool):
        self.result = result


class QuestActionsChanged(GameEvent):
    pass


class QuestFailedByTooManyRejections(GameEvent):
    pass


class QuestCompleted(GameEvent):
    def __init__(self, result: bool, failed_votes: int, success_votes: int):
        self.result = result
        self.failed_votes = failed_votes
        self.success_votes = success_votes


class GameDeleted(GameEvent):
    pass


class EventListener:
    def __init__(self, listener_id, game: Game):
        self.game = game
        self.game_id = game.game_id
        self.id = listener_id
        self.created = self.last_save = datetime.utcnow()
        self.queue = asyncio.Queue()

    async def save(self):
        self.last_save = datetime.utcnow()
        await redis_client.setex(config.REDIS_PREFIX_LISTENER + self.id, config.GAME_RETENTION, pickle.dumps(self))

    async def reload_game(self):
        self.game = await Game.load_by_id(self.game_id)
        assert self.game
        return self.game

    @asynccontextmanager
    async def listen(self):
        InMemoryPubSub.listeners[self.game_id].add(self)
        yield self
        InMemoryPubSub.listeners[self.game_id].remove(self)

    @staticmethod
    def lock(identity):
        return redis_client.lock(config.REDIS_PREFIX_LISTENER_LOCK + identity, timeout=120)

    @classmethod
    async def load_by_id(cls, listener_id: str) -> 'EventListener':
        value = await redis_client.get(config.REDIS_PREFIX_LISTENER + listener_id)
        if value:
            listener = pickle.loads(value)
            listener.game = await Game.load_by_id(listener.game_id)
            if listener and listener.game:
                return listener

    def __getstate__(self):
        state = self.__dict__.copy()
        del state["queue"]
        del state["game"]
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.game = None
        if not getattr(self, 'queue', None):
            self.queue = asyncio.Queue()


class InMemoryPubSub:
    listeners: dict[str, set[EventListener]] = defaultdict(weakref.WeakSet)

    @staticmethod
    def publish(game, event: GameEvent):
        for listener in InMemoryPubSub.listeners[game.game_id]:
            listener.queue.put_nowait(event)

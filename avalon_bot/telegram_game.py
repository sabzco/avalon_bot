import asyncio
import html
from itertools import zip_longest
from typing import TypeVar, Iterable, Optional

from telegram import User, InlineKeyboardMarkup, InlineKeyboardButton, Update, Bot, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest

from avalon.game import Participant, KING_EMOJI, LADY_EMOJI, SUCCESS_EMOJI, FAIL_EMOJI, GamePhase, ROLE_EMOJI, \
    Role, EventListener, Game
from avalon_bot.common import MSG_JOIN, MSG_LEAVE, MSG_PLAY, MSG_SELECT, MSG_MY_ROLE, MSG_CONFIRM_TEAM, \
    MSG_PROCEED, MSG_REJECT, MSG_APPROVE, MSG_SUCCESS, MSG_FAIL, MSG_NEXT_LADY, MSG_TRUTH, MSG_GUESS_MERLIN, \
    MSG_CONFIRM_MERLIN

T = TypeVar('T')

INT_EMOJI = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


async def send_ignore_400(coro):
    try:
        await coro
    except BadRequest as e:
        if 'Message is not modified' not in str(e):
            raise


def grouper(iterable: Iterable[T], n) -> list[list[T]]:
    """
    >>> grouper('ABCDEFG', 3)
    [('A', 'B', 'C'), ('D', 'E', 'F'), ('G',)]
    """
    args = [iter(iterable)] * n
    # noinspection PyTypeChecker
    return [list(filter(None, x)) for x in zip_longest(*args)]


class TgParticipant(Participant):
    def __init__(self, user: User):
        super().__init__(str(user.id))
        self.username = user.username
        self.full_name = user.full_name

    def __str__(self):
        return self.full_name


def mention(p: Participant):
    if isinstance(p, TgParticipant):
        return f'<a href="tg://user?id={p.identity}">{html.escape(p.full_name)}</a>'
    return str(p)


class TgListener(EventListener):
    def __init__(self, listener_id: str, game: Game):
        super().__init__(listener_id, game)
        self.active_message_id = None
        self.game_start_message_id = None
        self.last_vote_message_id = None
        self.last_quest_message_id = None
        self.pending_next_lady: Optional[TgParticipant] = None
        self.pending_merlin: Optional[TgParticipant] = None
        self.chat_id = int(listener_id)
        self.lady_responses = {}  # {message_id: {identity: str, is_evil: bool}}

    async def send_msg(self, update: Update, params: dict):
        orig_msg = update.callback_query.message if update.callback_query else update.message
        msg = await orig_msg.reply_text(**params, quote=False)
        self.message_sent(msg)
        return msg

    def message_sent(self, msg: Message):
        self.active_message_id = msg.message_id
        if self.game.phase == GamePhase.Started:
            asyncio.create_task(msg.get_bot().pin_chat_message(chat_id=msg.chat_id, message_id=msg.message_id))
            self.game_start_message_id = msg.message_id
        elif self.game_start_message_id:
            self.update_game_start_message(msg.get_bot())

        if self.game.phase == GamePhase.TeamVote:
            self.last_vote_message_id = msg.message_id
        if self.game.phase == GamePhase.Quest:
            self.last_quest_message_id = msg.message_id

    def update_game_start_message(self, bot: Bot):
        asyncio.create_task(
            send_ignore_400(bot.edit_message_text(chat_id=self.chat_id, message_id=self.game_start_message_id,
                                                  **self.get_game_start_message())))

    def set_next_lady(self, participant: T, next_identity: str, message=None, dry_run=False) -> T:
        lady = self.game.set_next_lady(participant, next_identity, dry_run)
        self.pending_next_lady = lady if dry_run else None
        if not dry_run:
            assert message
            self.lady_responses[message.message_id] = dict(identity=participant.identity, is_evil=lady.role.is_evil)
        return lady

    def guess_merlin(self, participant: T, identity: str, dry_run=False) -> T:
        merlin = self.game.guess_merlin(participant, identity, dry_run)
        self.pending_merlin = merlin if dry_run else None
        return merlin

    def get_current_phase_message(self):
        phase_to_func = {
            GamePhase.Joining: self.send_joining_message,
            GamePhase.Started: self.get_game_start_message,
            GamePhase.TeamBuilding: self.get_team_building_message,
            GamePhase.TeamVote: self.get_voting_phase_message,
            GamePhase.Quest: self.get_quest_message,
            GamePhase.Lady: self.get_lady_message,
            GamePhase.GuessMerlin: self.get_guess_merlin_message,
            GamePhase.Finished: self.get_finished_message,
        }
        return phase_to_func[self.game.phase]()

    def send_joining_message(self):
        msg = f'<i>Press <b>Play</b> after all participants have joined … ' \
              f'(Join key: <code>{self.game.game_id}</code>)</i>\n'
        for i, p in enumerate(self.game.participants):
            msg += f'\n‎{i + 1}. {mention(p)}'
        return dict(
            text=msg,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Join 🔺", callback_data=MSG_JOIN),
                 InlineKeyboardButton("Leave 🔻", callback_data=MSG_LEAVE)],
                [InlineKeyboardButton("Play 💥", callback_data=MSG_PLAY)]
            ]),
        )

    def get_game_start_message(self):
        msg = ["<i><b>The game is started!</b></i>\n"]
        for i, p in enumerate(self.game.participants):
            emoji = KING_EMOJI if self.game.king == p else (LADY_EMOJI if self.game.lady == p else "")
            msg.append(f'‎{i + 1}. {mention(p)} {emoji}')
        msg.append("\n<i>Roles (<b>not</b> necessarily in the order of the participants):</i>")
        for r in self.game.plan.roles:
            msg.append(f'{"▪️" if r.is_evil else "▫️"} {r.emoji} {r.value}')
        msg.append("\n<i>Quests:</i>")
        msg.append(' '.join(INT_EMOJI[step[1]] for step in self.game.plan.steps))
        msg.append(' '.join(INT_EMOJI[step[0]] for step in self.game.plan.steps))
        msg.append(' '.join((SUCCESS_EMOJI if step else FAIL_EMOJI) for step in self.game.round_result))
        if self.game.failed_voting_count:
            msg.append(f'\nFailed Voting Count: {self.game.failed_voting_count} of {len(self.game.participants)}')
        return dict(
            text='\n'.join(msg),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("My Role", callback_data=MSG_MY_ROLE)],
                [InlineKeyboardButton("Proceed To Game", callback_data=MSG_PROCEED)],
            ]),
        )

    def get_team_building_message(self):
        msg = f"<b>‎{KING_EMOJI} {mention(self.game.king)}!</b>\n<i>" + \
              f"Choose {self.game.step[1]} people for this quest!</i>\n"
        for i, p in enumerate(self.game.current_team):
            msg += f'\n‎{i + 1}. {mention(p)}'
        buttons = [InlineKeyboardButton(str(p), callback_data=MSG_SELECT + p.identity) for p in self.game.participants]
        return dict(
            text=msg,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(grouper(buttons, 2) +
                                              [[InlineKeyboardButton("CONFIRM ✅", callback_data=MSG_CONFIRM_TEAM)]]),
        )

    def get_voting_phase_message(self):
        msg = "<b><i>Vote for the this team:</i>\n"
        if self.game.failed_voting_count:
            msg += f'{self.game.failed_voting_count} rejection in this round (out of {len(self.game.participants)})\n'
        for p in self.game.current_team:
            msg += f'\n‎🏅 {mention(p)}'
        msg += "\n</b>"
        for p in self.game.participants:
            msg += f'\n‎{"❔" if p.vote is None else "🗳"} {mention(p)}'
        return dict(
            text=msg,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Approve ⚪", callback_data=MSG_APPROVE),
                InlineKeyboardButton("Reject ⚫", callback_data=MSG_REJECT),
            ]]),
        )

    def get_voting_result_message(self, results):
        msg = f'<b>Selected team is {"APPROVED! ✅" if results else "REJECTED! ❌"}</b>\n'
        for p in self.game.participants:
            msg += f'\n‎{"⚪" if p.vote else "⚫"} {mention(p)}'
        return dict(text=msg, parse_mode=ParseMode.HTML)

    def get_quest_message(self):
        msg = f"<i><b>Choose the battle result:</b>\n(fail votes to fail quest: {self.game.step[0]})</i>\n"
        for p in self.game.current_team:
            msg += f'\n‎{"❔" if p.quest_action is None else "🔱"} {mention(p)}'
        return dict(
            text=msg,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Success " + SUCCESS_EMOJI, callback_data=MSG_SUCCESS),
                InlineKeyboardButton("Fail " + FAIL_EMOJI, callback_data=MSG_FAIL),
            ]]),
        )

    def get_quest_result_message(self, succeeded: bool, failed_count: int, success_count: int):
        msg = ''
        for p in self.game.current_team:
            msg += f'‎{"❔" if p.quest_action is None else "🔱"} {mention(p)}\n'
        msg += f'<b>The quest is {"SUCCEEDED! ✅" if succeeded else "FAILED! ❌"}</b>\n'
        msg += SUCCESS_EMOJI * success_count
        msg += FAIL_EMOJI * failed_count
        return dict(text=msg, parse_mode=ParseMode.HTML)

    def get_lady_message(self):
        msg = f"<b>‎{LADY_EMOJI} {mention(self.game.lady)}!</b>\n<i>" + \
              f"Choose the next lady!\nYou will know his/her team.</i>\n\n" \
              f"New Lady 👉 ‎{mention(self.pending_next_lady) if self.pending_next_lady else '???'}"

        buttons = [InlineKeyboardButton(str(p), callback_data=MSG_NEXT_LADY + p.identity) for p in
                   self.game.next_lady_candidates()]
        return dict(
            text=msg,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(grouper(buttons, 2) +
                                              [[InlineKeyboardButton("Tell me the truth", callback_data=MSG_TRUTH)]]),
        )

    def get_guess_merlin_message(self):
        msg = ""
        for p in self.game.participants:
            if p.role.is_evil:
                msg += f'▪️ {p.role.value} {p.role.emoji} {p}\n'

        msg += f'\n<b>{mention(self.game.get_assassin())}</b>'
        msg += f'\n<i>Try to guess <b>Merlin</b> {ROLE_EMOJI[Role.Merlin]}!</i>\n'
        msg += f'\nMerlin 👉 ‎{self.pending_merlin or "???"}'
        buttons = [InlineKeyboardButton(str(p), callback_data=MSG_GUESS_MERLIN + p.identity)
                   for p in self.game.merlin_candidates()]
        return dict(
            text=msg,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(grouper(buttons, 2) +
                                              [[InlineKeyboardButton("CONFIRM ✅", callback_data=MSG_CONFIRM_MERLIN)]]),
        )

    def get_finished_message(self):
        # noinspection PyTypeChecker
        msg = (SUCCESS_EMOJI if self.game.game_result else FAIL_EMOJI) * 3 + '\n'
        for p in self.game.participants:
            msg += f'\n{"▪️" if p.role.is_evil else "▫️"} {p.role.value} {p.role.emoji} {str(p)}'
        msg += '\n\nStart a new game with /new or /restart'
        return dict(text=msg, parse_mode=ParseMode.HTML)

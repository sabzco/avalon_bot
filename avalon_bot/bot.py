import asyncio
import functools
import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Bot
from telegram.error import TelegramError
from telegram.ext import CallbackContext, CallbackQueryHandler, Application, CommandHandler

from avalon import config
from avalon.exceptions import InvalidActionException, OnlyLadyCanDo
from avalon.game import GamePhase, FAIL_EMOJI, Game, GameDeleted, GameEvent, VotingCompleted, \
    QuestFailedByTooManyRejections, GamePhaseChanged, VotesChanged, QuestCompleted
from avalon_bot.common import COMMAND_NEW, COMMAND_FINISH, MSG_START, MSG_JOIN, MSG_LEAVE, MSG_PLAY, MSG_PROCEED, \
    MSG_SELECT, MSG_CONFIRM_TEAM, MSG_MY_ROLE, MSG_APPROVE, MSG_REJECT, MSG_SUCCESS, MSG_FAIL, MSG_NEXT_LADY, \
    MSG_TRUTH, MSG_GUESS_MERLIN, MSG_CONFIRM_MERLIN, COMMAND_RESTART
from avalon_bot.telegram_game import TgParticipant, send_ignore_400, TgListener

logger = logging.getLogger(__name__)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.DEBUG)

Update.any_reply_text = lambda u, *a, **kw: \
    (u.message.reply_text if u.message else u.callback_query.answer)(*a, **kw)


def later_edit(update: Update, **kwargs):
    msg = update.callback_query.message
    asyncio.create_task(
        send_ignore_400(msg.get_bot().edit_message_text(chat_id=msg.chat_id, message_id=msg.message_id, **kwargs)))


class ListenerManager:
    def __init__(self, bot: Bot):
        self.chat_tasks = {}
        self.bot = bot

    async def load_listener(self, chat) -> TgListener:
        listener = await TgListener.load_by_id(str(chat.id))
        if listener and chat.id not in self.chat_tasks:
            self.chat_tasks[chat.id] = asyncio.create_task(self.listen(listener))
        return listener

    async def listen(self, listener: TgListener):
        try:
            async with listener.listen():
                while True:
                    event: GameEvent = await listener.queue.get()
                    try:
                        async with TgListener.lock(listener.id):
                            tg_listener = await TgListener.load_by_id(listener.id)
                            if isinstance(event, GameDeleted) or not tg_listener:
                                break
                            await send_ignore_400(self.process_game_event(event, tg_listener))
                    except TelegramError:
                        logger.exception('TelegramError on listener')
        finally:
            self.chat_tasks.pop(listener.chat_id)

    async def process_game_event(self, event, tg_listener):
        if isinstance(event, VotesChanged):
            await self.bot.edit_message_text(chat_id=tg_listener.chat_id, message_id=tg_listener.last_vote_message_id,
                                             **tg_listener.get_voting_phase_message())
        elif isinstance(event, VotingCompleted):
            await self.bot.send_message(chat_id=tg_listener.chat_id,
                                        **tg_listener.get_voting_result_message(event.result))
        elif isinstance(event, QuestFailedByTooManyRejections):
            await self.bot.send_message(chat_id=tg_listener.chat_id,
                                        text=f'{FAIL_EMOJI} Too many rejections, quest failed')
        elif isinstance(event, GamePhaseChanged):
            msg = await self.bot.send_message(chat_id=tg_listener.chat_id, **tg_listener.get_current_phase_message())
            tg_listener.message_sent(msg)
            await tg_listener.save()
        elif isinstance(event, QuestCompleted):
            params = tg_listener.get_quest_result_message(event.result, event.failed_votes, event.success_votes)
            await self.bot.send_message(chat_id=tg_listener.chat_id, **params)
        else:  # GameParticipantsChanged, QuestTeamChanged, QuestActionsChanged
            await self.bot.edit_message_text(chat_id=tg_listener.chat_id, message_id=tg_listener.active_message_id,
                                             **tg_listener.get_current_phase_message())


# noinspection PyTypeChecker
listener_manager: ListenerManager = None


async def start_bot(update: Update, _context: CallbackContext.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    async with TgListener.lock(str(chat.id)):
        tg_listener = await listener_manager.load_listener(chat)
        if tg_listener:
            await tg_listener.send_msg(update, tg_listener.get_current_phase_message())
            await tg_listener.save()
        else:
            logger.debug(f'/start from user:{user.id} chat:{chat.id}')
            # noinspection SpellCheckingInspection
            await update.message.reply_photo(
                'AgACAgQAAxUAAWKaFG4UiZG61Ypizt8emZo6lMGCAAICtjEbFIchUY9MUzdRt845AQADAgADcwADJAQ',
                caption="Welcome To Avalon Bot", quote=False,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Play now 💥", callback_data=MSG_START)],
                    [InlineKeyboardButton("Add me to a Group", url=update.message.get_bot().link + '?startgroup=new')]
                ])
            )


async def finish_game(update: Update, _context: CallbackContext.DEFAULT_TYPE):
    tg_listener = await TgListener.load_by_id(str(update.effective_chat.id))
    if tg_listener:
        await tg_listener.game.delete()
        await update.message.reply_text(f'Game is finished, start a new one with /{COMMAND_NEW}')
    else:
        await update.message.reply_text(f'No game is in progress, start a one with /{COMMAND_NEW}')


async def start_game(update: Update, _context: CallbackContext.DEFAULT_TYPE):
    tg_listener = await listener_manager.load_listener(update.effective_chat)
    if tg_listener and tg_listener.game.phase != GamePhase.Finished:
        await update.any_reply_text(f'A game is already in progress, send /{COMMAND_FINISH} or /{COMMAND_RESTART}')
    else:
        if update.callback_query:
            asyncio.create_task(update.callback_query.answer())
        game = Game(participants=[TgParticipant(update.effective_user)])
        tg_listener = TgListener(str(update.effective_chat.id), game)
        await tg_listener.send_msg(update, tg_listener.send_joining_message())
        await game.save()
        await tg_listener.save()
        await listener_manager.load_listener(update.effective_chat)


async def restart_game(update: Update, _context: CallbackContext.DEFAULT_TYPE):
    tg_listener = await TgListener.load_by_id(str(update.effective_chat.id))
    if not tg_listener:
        game = Game(participants=[TgParticipant(update.effective_user)])
        tg_listener = TgListener(str(update.effective_chat.id), game)
    tg_listener.game.restart()
    await tg_listener.send_msg(update, tg_listener.send_joining_message())
    await tg_listener.game.save()
    await listener_manager.load_listener(update.effective_chat)


def game_query_callback(f=None, create_new_participant=False, check_for_active_message=True):
    if f is None:
        return functools.partial(game_query_callback, create_new_participant=create_new_participant,
                                 check_for_active_message=check_for_active_message)

    @functools.wraps(f)
    async def wrapped(update: Update, context: CallbackContext.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        async with TgListener.lock(chat_id):
            tg_listener = await listener_manager.load_listener(update.effective_chat)
            if not tg_listener:
                answer = f'No game is in progress, start a new one with /{COMMAND_NEW}'
            elif not update.callback_query or not update.callback_query.message:
                answer = 'Unknown button pressed'
            elif check_for_active_message and update.callback_query.message.message_id != tg_listener.active_message_id:
                answer = 'Button pressed on an old message'
            else:
                async with Game.lock(tg_listener.game.game_id):
                    # noinspection PyBroadException
                    try:
                        actor = TgParticipant(update.effective_user) if create_new_participant else \
                            tg_listener.game.get_participant_by_id(str(update.effective_user.id))
                        answer = await f(tg_listener.game, actor, tg_listener, update, context)
                        await tg_listener.save()
                    except InvalidActionException as e:
                        answer = str(e)
                    except Exception:
                        logger.exception('Unhandled Error')
                        answer = 'Unhandled Error'
        if isinstance(answer, dict):
            await update.callback_query.answer(**answer)
        else:
            await update.callback_query.answer(answer)

    return wrapped


@game_query_callback(create_new_participant=True)
async def join(game: Game, actor: TgParticipant, *_args):
    game.add_participant(actor)
    await game.save()
    return 'Joined successfully'


@game_query_callback(create_new_participant=True)
async def leave(game: Game, actor: TgParticipant, *_args):
    game.remove_participant(actor)
    await game.save()
    return 'Left successfully'


@game_query_callback
async def play(game: Game, *_args):
    game.play()
    await game.save()


@game_query_callback(check_for_active_message=False)
async def my_info(_tg_listener: TgListener, game: Game, actor, *_):
    return dict(text=game.get_user_info(actor), show_alert=True)


@game_query_callback
async def proceed(game: Game, *_args):
    game.proceed_to_game()
    await game.save()


@game_query_callback
async def select(game: Game, actor: TgParticipant, _tg_listener: TgListener, update: Update, _context):
    identity = update.callback_query.data.replace(MSG_SELECT, '')
    game.select_for_team(actor, identity)
    await game.save()


@game_query_callback
async def confirm_team(game: Game, actor: TgParticipant, *_args):
    game.confirm_team(actor)
    await game.save()


@game_query_callback
async def vote(game: Game, actor: TgParticipant, _tg_listener: TgListener, update: Update, _context):
    game.vote(actor, update.callback_query.data == MSG_APPROVE)
    game.process_vote_results()
    await game.save()
    return 'Current vote: ' + actor.current_vote_text


@game_query_callback
async def quest_action(game: Game, actor: TgParticipant, _tg_listener: TgListener, update: Update, _context):
    game.quest_action(actor, update.callback_query.data == MSG_SUCCESS)
    game.process_quest_result()
    await game.save()
    return 'Current action: ' + actor.current_vote_text


@game_query_callback
async def select_next_lady(_game: Game, actor: TgParticipant, tg_listener: TgListener, update: Update, _context):
    identity = update.callback_query.data.replace(MSG_NEXT_LADY, '')
    next_lady = tg_listener.set_next_lady(actor, identity, dry_run=True)
    await send_ignore_400(update.callback_query.message.edit_text(**tg_listener.get_lady_message()))
    return 'Next lady will be: ' + str(next_lady)


@game_query_callback(check_for_active_message=False)
async def get_lady_truth(game: Game, actor: TgParticipant, tg_listener: TgListener, update: Update, _context):
    if update.callback_query.message.message_id != tg_listener.active_message_id:
        # Button pressed on an old message
        result = tg_listener.lady_responses.get(update.callback_query.message.message_id)
        if not result:
            return 'Button pressed on an old message'
        if result['identity'] != actor.identity:
            raise OnlyLadyCanDo
        return dict(text=f'He/She is {"" if result["is_evil"] else "NOT "}an EVIL!', show_alert=True)

    # current phase should be Lady, proceed the game
    if not tg_listener.pending_next_lady:
        raise InvalidActionException('No one is selected')
    next_lady = tg_listener.set_next_lady(actor, tg_listener.pending_next_lady.identity,
                                          message=update.callback_query.message)
    await game.save()
    return dict(text=f'He/She is {"" if next_lady.role.is_evil else "NOT "}an EVIL!', show_alert=True)


@game_query_callback
async def guess_merlin(_game: Game, actor: TgParticipant, tg_listener: TgListener, update: Update, _context):
    identity = update.callback_query.data.replace(MSG_GUESS_MERLIN, '')
    tg_listener.guess_merlin(actor, identity, dry_run=True)
    await send_ignore_400(update.callback_query.message.edit_text(**tg_listener.get_guess_merlin_message()))


@game_query_callback
async def confirm_merlin(game: Game, actor: TgParticipant, tg_listener: TgListener, *_args):
    if not tg_listener.pending_merlin:
        raise InvalidActionException('Merlin not selected')
    tg_listener.guess_merlin(actor, tg_listener.pending_merlin.identity)
    await game.save()


def main():
    global listener_manager
    app = (Application.builder()
           .token(config.BOT_TOKEN)
           .proxy_url(config.BOT_PROXY)
           .get_updates_proxy_url(config.BOT_PROXY)
           .build())
    listener_manager = ListenerManager(app.bot)
    app.add_handler(CommandHandler('start', start_bot))
    app.add_handler(CommandHandler(COMMAND_FINISH, finish_game))
    app.add_handler(CommandHandler(COMMAND_NEW, start_game))
    app.add_handler(CommandHandler(COMMAND_RESTART, restart_game))
    app.add_handler(CallbackQueryHandler(start_game, MSG_START))
    app.add_handler(CallbackQueryHandler(join, MSG_JOIN))
    app.add_handler(CallbackQueryHandler(leave, MSG_LEAVE))
    app.add_handler(CallbackQueryHandler(play, MSG_PLAY))
    app.add_handler(CallbackQueryHandler(my_info, MSG_MY_ROLE))
    app.add_handler(CallbackQueryHandler(proceed, MSG_PROCEED))
    app.add_handler(CallbackQueryHandler(select, MSG_SELECT + '.*'))
    app.add_handler(CallbackQueryHandler(confirm_team, MSG_CONFIRM_TEAM))
    app.add_handler(CallbackQueryHandler(vote, MSG_APPROVE))
    app.add_handler(CallbackQueryHandler(vote, MSG_REJECT))
    app.add_handler(CallbackQueryHandler(quest_action, MSG_SUCCESS))
    app.add_handler(CallbackQueryHandler(quest_action, MSG_FAIL))
    app.add_handler(CallbackQueryHandler(select_next_lady, MSG_NEXT_LADY + '.*'))
    app.add_handler(CallbackQueryHandler(get_lady_truth, MSG_TRUTH))
    app.add_handler(CallbackQueryHandler(guess_merlin, MSG_GUESS_MERLIN))
    app.add_handler(CallbackQueryHandler(confirm_merlin, MSG_CONFIRM_MERLIN))
    app.run_polling()


if __name__ == '__main__':
    main()

import asyncio
import functools
import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackContext, CallbackQueryHandler, Application, CommandHandler

from avalon import config
from avalon.exceptions import InvalidActionException, OnlyLadyCanDo
from avalon.game import GamePhase, FAIL_EMOJI
from avalon_bot.common import COMMAND_NEW, COMMAND_FINISH, MSG_START, MSG_JOIN, MSG_LEAVE, MSG_PLAY, MSG_PROCEED, \
    MSG_SELECT, MSG_CONFIRM_TEAM, MSG_MY_ROLE, MSG_APPROVE, MSG_REJECT, MSG_SUCCESS, MSG_FAIL, MSG_NEXT_LADY, \
    MSG_TRUTH, MSG_GUESS_MERLIN, MSG_CONFIRM_MERLIN
from avalon_bot.telegram_game import TgParticipant, TgGame, send_ignore_400

logger = logging.getLogger(__name__)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.DEBUG)

Update.any_reply_text = lambda u, *a, **kw: \
    (u.message.reply_text if u.message else u.callback_query.answer)(*a, **kw)


def later_edit(update: Update, **kwargs):
    msg = update.callback_query.message
    asyncio.create_task(
        send_ignore_400(msg.get_bot().edit_message_text(chat_id=msg.chat_id, message_id=msg.message_id, **kwargs)))


async def start_bot(update: Update, _context: CallbackContext.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    async with TgGame.lock(str(chat.id)):
        game = await TgGame.load_by_id(str(chat.id))
        if game:
            await game.send_msg(update, game.get_current_phase_message())
            await game.save()
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
    game = await TgGame.load_by_id(str(update.effective_chat.id))
    if game:
        await game.delete()
        await update.message.reply_text(f'Game is finished, start a new one with /{COMMAND_NEW}')
    else:
        await update.message.reply_text(f'No game is in progress, start a one with /{COMMAND_NEW}')


async def start_game(update: Update, _context: CallbackContext.DEFAULT_TYPE):
    game = await TgGame.load_by_id(str(update.effective_chat.id))
    if game and game.phase != GamePhase.Finished:
        await update.any_reply_text(f'A game is already in progress, send /{COMMAND_FINISH} to stop it')
    else:
        if update.callback_query:
            asyncio.create_task(update.callback_query.answer())
        game = TgGame(str(update.effective_chat.id))
        game.add_participant(TgParticipant(update.effective_user))
        await game.send_msg(update, game.send_joining_message())
        await game.save()


def game_query_callback(f=None, create_new_participant=False, check_for_active_message=True):
    if f is None:
        return functools.partial(game_query_callback, create_new_participant=create_new_participant,
                                 check_for_active_message=check_for_active_message)

    @functools.wraps(f)
    async def wrapped(update: Update, context: CallbackContext.DEFAULT_TYPE):
        game_id = str(update.effective_chat.id)
        async with TgGame.lock(game_id):
            game = await TgGame.load_by_id(game_id)
            if not game:
                answer = f'No game is in progress, start a new one with /{COMMAND_NEW}'
            elif not update.callback_query or not update.callback_query.message:
                answer = 'Unknown button pressed'
            elif check_for_active_message and update.callback_query.message.message_id != game.active_message_id:
                answer = 'Button pressed on an old message'
            else:
                # noinspection PyBroadException
                try:
                    actor = TgParticipant(update.effective_user) \
                        if create_new_participant else game.get_participant_by_id(str(update.effective_user.id))
                    answer = f(game, actor, update, context)
                    if asyncio.iscoroutine(answer):
                        answer = await answer
                    await game.save()
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
def join(game: TgGame, actor: TgParticipant, update: Update, _context: CallbackContext.DEFAULT_TYPE):
    game.add_participant(actor)
    later_edit(update, **game.send_joining_message())
    return 'Joined successfully'


@game_query_callback(create_new_participant=True)
def leave(game: TgGame, actor: TgParticipant, update: Update, _context: CallbackContext.DEFAULT_TYPE):
    game.remove_participant(actor)
    later_edit(update, **game.send_joining_message())
    return 'Left successfully'


@game_query_callback
async def play(game: TgGame, _participant, update: Update, _context):
    game.play()
    await game.send_msg(update, game.get_current_phase_message())


@game_query_callback(check_for_active_message=False)
def my_info(game: TgGame, actor, *_):
    return dict(text=game.get_user_info(actor), show_alert=True)


@game_query_callback
async def proceed(game: TgGame, _participant, update: Update, _context):
    game.proceed_to_game()
    await game.send_msg(update, game.get_current_phase_message())


@game_query_callback
def select(game: TgGame, actor: TgParticipant, update: Update, _context):
    identity = update.callback_query.data.replace(MSG_SELECT, '')
    game.select_for_team(actor, identity)
    later_edit(update, **game.get_team_building_message())


@game_query_callback
async def confirm_team(game: TgGame, actor: TgParticipant, update: Update, _context):
    game.confirm_team(actor)
    await game.send_msg(update, game.get_current_phase_message())


@game_query_callback
async def vote(game: TgGame, actor: TgParticipant, update: Update, _context):
    game.vote(actor, update.callback_query.data == MSG_APPROVE)
    later_edit(update, **game.get_voting_phase_message())
    results = game.process_vote_results()
    if results is not None:
        await update.callback_query.message.reply_text(**game.get_voting_result_message(results), quote=False)
        if results is False and game.failed_voting_count == 0:
            await update.callback_query.message.reply_text(f'{FAIL_EMOJI} Too many rejections, quest failed',
                                                           quote=False)
        await game.send_msg(update, game.get_current_phase_message())
    return 'Current vote: ' + actor.current_vote_text


@game_query_callback
async def quest_action(game: TgGame, actor: TgParticipant, update: Update, _context):
    game.quest_action(actor, update.callback_query.data == MSG_SUCCESS)
    later_edit(update, **game.get_quest_message())
    results = game.process_quest_result()
    if results is not None:
        await update.callback_query.message.reply_text(**game.get_quest_result_message(*results), quote=False)
        await game.send_msg(update, game.get_current_phase_message())
    return 'Current action: ' + actor.current_vote_text


@game_query_callback
async def select_next_lady(game: TgGame, actor: TgParticipant, update: Update, _context):
    identity = update.callback_query.data.replace(MSG_NEXT_LADY, '')
    next_lady = game.set_next_lady(actor, identity, dry_run=True)
    await send_ignore_400(update.callback_query.message.edit_text(**game.get_lady_message()))
    return 'Next lady will be: ' + str(next_lady)


@game_query_callback(check_for_active_message=False)
async def get_lady_truth(game: TgGame, actor: TgParticipant, update: Update, _context):
    if update.callback_query.message.message_id != game.active_message_id:
        # Button pressed on an old message
        result = game.lady_responses.get(update.callback_query.message.message_id)
        if not result:
            return 'Button pressed on an old message'
        if result['identity'] != actor.identity:
            raise OnlyLadyCanDo
        return dict(text=f'He/She is {"" if result["is_evil"] else "NOT "}an EVIL!', show_alert=True)

    # current phase should be Lady, proceed the game
    if not game.pending_next_lady:
        raise InvalidActionException('No one is selected')
    next_lady = game.set_next_lady(actor, game.pending_next_lady.identity, message=update.callback_query.message)
    await game.send_msg(update, game.get_current_phase_message())
    return dict(text=f'He/She is {"" if next_lady.role.is_evil else "NOT "}an EVIL!', show_alert=True)


@game_query_callback
async def guess_merlin(game: TgGame, actor: TgParticipant, update: Update, _context):
    identity = update.callback_query.data.replace(MSG_GUESS_MERLIN, '')
    game.guess_merlin(actor, identity, dry_run=True)
    await send_ignore_400(update.callback_query.message.edit_text(**game.get_guess_merlin_message()))


@game_query_callback
async def confirm_merlin(game: TgGame, actor: TgParticipant, update: Update, _context):
    if not game.pending_merlin:
        raise InvalidActionException('Merlin not selected')
    game.guess_merlin(actor, game.pending_merlin.identity)
    await game.send_msg(update, game.get_current_phase_message())


def main():
    app = (Application.builder()
           .token(config.BOT_TOKEN)
           .proxy_url(config.BOT_PROXY)
           .get_updates_proxy_url(config.BOT_PROXY)
           .build())
    app.add_handler(CommandHandler('start', start_bot))
    app.add_handler(CommandHandler(COMMAND_FINISH, finish_game))
    app.add_handler(CommandHandler(COMMAND_NEW, start_game))
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

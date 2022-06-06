from avalon.game import Participant, KING_EMOJI, LADY_EMOJI, SUCCESS_EMOJI, FAIL_EMOJI, GamePhase, ROLE_EMOJI, \
    Role, EventListener

KING_TEXT = 'KING'
LADY_TEXT = 'LADY'


class SshParticipant(Participant):
    def __init__(self, username, identity):
        super().__init__(identity)
        self.username = username

    def __str__(self):
        return self.username


class SshListener(EventListener):
    def get_current_phase_message(self):
        phase_to_func = {
            GamePhase.Joining: self.get_joining_message,
            GamePhase.Started: self.get_game_start_message,
            GamePhase.TeamBuilding: self.get_team_building_message,
            GamePhase.TeamVote: self.get_voting_phase_message,
            GamePhase.Quest: self.get_quest_message,
            GamePhase.Lady: self.get_lady_message,
            GamePhase.GuessMerlin: self.get_guess_merlin_message,
            GamePhase.Finished: self.get_finished_message,
        }
        return phase_to_func[self.game.phase]()

    @property
    def actor_id(self):
        return self.id

    def is_me(self, participant):
        return isinstance(participant, SshParticipant) and self.actor_id == participant.identity

    def get_joining_message(self):
        msg = 'Current Participants:\n'
        for i, p in enumerate(self.game.participants):
            msg += f'  {i + 1}. {p}\n'
        return msg

    def get_game_start_message(self):
        msg = ["The game is started!\n"]
        for i, p in enumerate(self.game.participants):
            emoji = KING_TEXT if self.game.king == p else (LADY_TEXT if self.game.lady == p else "")
            msg.append(f'‎{i + 1}. {p} {emoji}')
        msg.append("\nRoles (not necessarily in the order of the participants):")
        for r in self.game.plan.roles:
            msg.append(f'{"-" if r.is_evil else "+"} {r.emoji} {r.value}')
        msg.append("\nQuests: " + '  '.join('{}/{}'.format(*step) for step in self.game.plan.steps))
        if self.game.round_result:
            msg.append('Round Results: ' + ' '.join(
                (SUCCESS_EMOJI + 'SUCCESS' if step else FAIL_EMOJI + 'FAIL') for step in self.game.round_result))
        if self.game.failed_voting_count:
            msg.append(f'\nFailed Voting Count: {self.game.failed_voting_count} of {len(self.game.participants)}')
        return '\n'.join(msg) + '\n'

    def get_team_building_message(self):
        if self.is_me(self.game.king):
            msg = f"‎{KING_EMOJI} {self.game.king}!\n" + \
                  f"Choose {self.game.step[1]} people for this quest!\n\n"
            for i, p in enumerate(self.game.participants):
                msg += f'  {"@" if p in self.game.current_team else "."} ‎{i + 1}) {p}\n'
        else:
            msg = f"Wait for ‎{KING_EMOJI} {self.game.king} to choose the team!\n"
            msg += 'Current selection:\n' if self.game.current_team else ''
            for i, p in enumerate(self.game.current_team):
                msg += f' -‎ {p}\n'
        return msg

    def get_voting_phase_message(self):
        msg = "Vote for the this team:\n"
        if self.game.failed_voting_count:
            msg += f'{self.game.failed_voting_count} rejection in this round (out of {len(self.game.participants)})\n'
        for p in self.game.current_team:
            msg += f'\n‎🏅 {p}'
        msg += "\n"
        for p in self.game.participants:
            msg += f'\n ‎{"." if p.vote is None else "@"} {p}'
        return msg + '\n'

    def get_voting_result_message(self, results):
        msg = f'Selected team is {"APPROVED! ✅" if results else "REJECTED! ❌"}\n'
        for p in self.game.participants:
            msg += f'\n ‎{"+" if p.vote else "-"} {p}'
        return msg + '\n'

    def get_quest_message(self):
        for p in self.game.current_team:
            if p.identity == self.actor_id:
                msg = f"Choose the battle result:\n(fail votes to fail quest: {self.game.step[0]})\n"
                break
        else:
            msg = f"Wait for battle result.\n(fail votes to fail quest: {self.game.step[0]})\n"
        for p in self.game.current_team:
            msg += f'  ‎{"?" if p.quest_action is None else "."} {p}\n'
        return msg

    def get_quest_result_message(self, succeeded: bool, failed_count: int, success_count: int):
        msg = f'The quest is {"SUCCEEDED! ✅" if succeeded else "FAILED! ❌"} with {failed_count} fail(s).\n'
        msg += SUCCESS_EMOJI * success_count
        msg += FAIL_EMOJI * failed_count
        return msg + '\n'

    def get_lady_message(self):
        if not self.is_me(self.game.lady):
            return f"Wait for ‎{LADY_EMOJI} {self.game.lady} to choose next lady!\n"
        msg = f"‎{LADY_EMOJI} {self.game.lady}!\n" + \
              f"Choose the next lady!\nYou will know his/her team.\n"
        for i, p in enumerate(self.game.next_lady_candidates()):
            msg += f'\n  ‎{i + 1}) {p}'
        return msg + '\n'

    def get_guess_merlin_message(self):
        msg = ""
        for p in self.game.participants:
            if p.role.is_evil:
                msg += f' - {p.role.value} {p.role.emoji} {p}\n'

        if self.is_me(self.game.get_assassin()):
            msg += f'\n{(self.game.get_assassin())}!\n'
            msg += f'Try to guess Merlin {ROLE_EMOJI[Role.Merlin]}!\n\n'
            for i, p in enumerate(self.game.merlin_candidates()):
                msg += f'  ‎{i + 1}) {p}\n'
        else:
            msg += f'\nWait for {(self.game.get_assassin())} to guess Merlin {ROLE_EMOJI[Role.Merlin]}!\n'
        return msg

    def get_finished_message(self):
        # noinspection PyTypeChecker
        msg = 'SUCCESS ' if self.game.game_result else 'FAIL '
        msg += (SUCCESS_EMOJI if self.game.game_result else FAIL_EMOJI) * 3
        msg += '\n\n'
        for p in self.game.participants:
            msg += f'{"▪️" if p.role.is_evil else "▫️"} {p.role.value} {p.role.emoji} {str(p)}\n'
        msg += 'Start a new game with /new or /restart\n'
        return msg

class InvalidActionException(Exception):
    msg = ''

    def __str__(self):
        return str(self.args[0] if self.args else self.msg)


class AlreadyJoined(InvalidActionException):
    msg = "You've already joined"


class NotJoined(InvalidActionException):
    msg = "You are not joined"


class InvalidActionInThisPhase(InvalidActionException):
    msg = "Invalid actions in the current game state"


class OnlyKingCanDo(InvalidActionException):
    msg = 'Only king can do this'


class OnlyLadyCanDo(InvalidActionException):
    msg = 'Only lady can do this'


class OnlyAssassinCanDo(InvalidActionException):
    msg = 'Only assassin can do this'


class InvalidParticipant(InvalidActionException):
    msg = 'Not a game participant'

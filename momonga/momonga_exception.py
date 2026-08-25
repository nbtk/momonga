class MomongaError(Exception):
    pass


class MomongaSkCommandExecutionFailure(MomongaError):
    pass


class MomongaSkCommandUnknownError(MomongaSkCommandExecutionFailure):
    pass


class MomongaSkCommandUnsupported(MomongaSkCommandExecutionFailure):
    pass


class MomongaSkCommandInvalidArgument(MomongaSkCommandExecutionFailure):
    pass


class MomongaSkCommandInvalidSyntax(MomongaSkCommandExecutionFailure):
    pass


class MomongaSkCommandSerialInputError(MomongaSkCommandExecutionFailure):
    pass


class MomongaSkCommandFailedToExecute(MomongaSkCommandExecutionFailure):
    pass


class MomongaConnectionFailure(MomongaError):
    pass


class MomongaSkScanFailure(MomongaConnectionFailure):
    pass


class MomongaSkJoinFailure(MomongaConnectionFailure):
    pass


class MomongaRuntimeError(MomongaError, RuntimeError):
    pass


class MomongaValueError(MomongaError, ValueError):
    pass


class MomongaTimeoutError(MomongaConnectionFailure, TimeoutError):
    pass


class MomongaIOError(MomongaConnectionFailure, OSError):
    pass


class MomongaSkResponseNotExpected(MomongaConnectionFailure):
    pass


class MomongaKeyError(MomongaSkResponseNotExpected, KeyError):
    pass


class MomongaNeedToReopen(MomongaError):
    pass


class MomongaSkCommandCancelled(MomongaNeedToReopen):
    pass


class MomongaXmitTimeout(MomongaNeedToReopen):
    pass


class MomongaSkCommandBusy(MomongaNeedToReopen):
    pass


class MomongaResponseNotExpected(MomongaError):
    pass


class MomongaResponseNotPossible(MomongaError):
    pass

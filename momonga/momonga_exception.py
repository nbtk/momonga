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

class MomongaSkScanFailure(MomongaError):
    pass

class MomongaSkJoinFailure(MomongaError):
    pass

class MomongaTimeoutError(TimeoutError):
    pass

class MomongaKeyError(KeyError):
    pass

class MomongaNeedToReopen(MomongaError):
    pass

class MomongaResponseNotExpected(MomongaError):
    pass

class MomongaResponseNotPossible(MomongaError):
    pass

class MomongaError(Exception):
    """Base of every exception this library raises.

    Catch it to catch anything from momonga, whatever the cause.
    """


class MomongaSkCommandExecutionFailure(MomongaError):
    """The Wi-SUN module refused a command (FAIL ERxx).

    Which subclass depends on the error code. During packet transmission these
    are absorbed by retrying, and running out of retries raises
    MomongaNeedToReopen, so what reaches a caller comes mostly from open() -
    a Route-B ID or password in the wrong form, for instance. Neither a
    connection failure nor a session one: reopening will not help, so check
    what was passed.
    """


class MomongaSkCommandUnknownError(MomongaSkCommandExecutionFailure):
    """The module refused a command with a code that names no cause.

    ER01 to ER03, ER07, ER08, anything above ER10, or a response the code
    could not be read out of at all.
    """


class MomongaSkCommandUnsupported(MomongaSkCommandExecutionFailure):
    """The module does not implement the command (ER04)."""


class MomongaSkCommandInvalidArgument(MomongaSkCommandExecutionFailure):
    """An argument was outside what the command accepts (ER05)."""


class MomongaSkCommandInvalidSyntax(MomongaSkCommandExecutionFailure):
    """The command was not written the way the module expects (ER06)."""


class MomongaSkCommandSerialInputError(MomongaSkCommandExecutionFailure):
    """The module could not read the command off the serial line (ER09)."""


class MomongaSkCommandFailedToExecute(MomongaSkCommandExecutionFailure):
    """The module accepted the command and then failed to carry it out (ER10)."""


class MomongaConnectionFailure(MomongaError):
    """Base of the failures below the session - the radio and the device.

    Raised as MomongaSkScanFailure, MomongaSkJoinFailure, MomongaTimeoutError,
    MomongaIOError or MomongaSkResponseNotExpected. The causes differ but the
    answer does not: wait, then try again. Catch this rather than listing them.

    Never raised on its own. Its counterpart is MomongaNeedToReopen, which is
    the session rather than what carries it.
    """


class MomongaSkScanFailure(MomongaConnectionFailure):
    """No PAN answered the scan.

    Check that the meter is in range and that the Route-B ID is right, then
    try again.
    """


class MomongaSkJoinFailure(MomongaConnectionFailure):
    """A PAN answered but the PANA session never came up.

    Check the Route-B ID and password, then try again.
    """


class MomongaRuntimeError(MomongaError, RuntimeError):
    """The library was used in a way that cannot work.

    Issuing a request before open(), for instance. Also a RuntimeError.
    Reopening will not help - fix the call.
    """


class MomongaValueError(MomongaError, ValueError):
    """An argument was outside the range that is accepted.

    scan_retries of 0, a negative delay in reopen_delays, a day outside 0 to
    99. Also a ValueError. Like MomongaRuntimeError this is the call's fault -
    fix the value rather than retrying.
    """


class MomongaTimeoutError(MomongaConnectionFailure, TimeoutError):
    """The Wi-SUN module did not answer during open().

    Check the device path and that the module is attached.

    Also a TimeoutError, which since Python 3.11 is what asyncio.TimeoutError
    is: wrapping an await in asyncio.wait_for() and catching that will not
    tell this apart from your own deadline. Catch this one first if the
    difference matters. MomongaXmitTimeout and MomongaSkCommandBusy do not
    inherit TimeoutError, so they do not have the problem.
    """


class MomongaIOError(MomongaConnectionFailure, OSError):
    """The serial device itself failed.

    A missing device file, no permission, a dongle pulled out. pyserial's
    SerialException and the OS's own errors are wrapped in this, so callers
    need not import pyserial; the original is in __cause__.

    Also an OSError, and covered by reopen_delays.
    """


class MomongaSkResponseNotExpected(MomongaConnectionFailure):
    """The Wi-SUN module's response could not be read.

    A field missing, a value that is not the hexadecimal it should be. Noise
    or a dropped byte on the serial line, so it is a MomongaConnectionFailure:
    wait and try again.

    This is the module's response. The meter's is MomongaResponseNotExpected.

    A PAN description that cannot be read during a scan does not raise this -
    the scan is simply run again, and running out of scan_retries raises
    MomongaSkScanFailure.
    """


class MomongaKeyError(MomongaSkResponseNotExpected, KeyError):
    """A field the module's response should have carried was not there.

    Also a KeyError. Catch MomongaSkResponseNotExpected instead if a missing
    field need not be told apart from an unreadable one.
    """


class MomongaNeedToReopen(MomongaError):
    """Base of the failures that need the session built again.

    Also raised on its own when no answer arrives, or the thread publishing
    packets has stopped. Raised as MomongaXmitTimeout, MomongaSkCommandBusy,
    MomongaSkCommandCancelled or MomongaSkCommandDeadlineExceeded otherwise.
    The causes differ but the answer does not: build a new session, or set
    reopen_delays and let the library do it.

    Its counterpart is MomongaConnectionFailure, which is what carries the
    session rather than the session itself.
    """


class MomongaSkCommandCancelled(MomongaNeedToReopen):
    """close() cut short an SK command that was running."""


class MomongaSkCommandDeadlineExceeded(MomongaNeedToReopen):
    """The time allowed for an SK command was gone before it started.

    close()'s SKTERM, a rejoin's SKJOIN and a packet's SKSENDTO each carry a
    deadline; past it, the command is not sent at all.

    Easy to confuse with MomongaSkCommandBusy and not the same thing: that one
    means another command held the lock, this one means the time ran out
    whatever the lock was doing.

    It does not inherit TimeoutError, so asyncio.wait_for() will not confuse it
    with a deadline of your own.
    """


class MomongaXmitTimeout(MomongaNeedToReopen):
    """No transmission right came free within xmit_timeout seconds."""


class MomongaSkCommandBusy(MomongaNeedToReopen):
    """Another SK command was running and this one could not start in time."""


class MomongaResponseNotExpected(MomongaError):
    """The meter's response could not be read.

    A property shorter than its declared length, a property code that does not
    match what was asked. The session is intact, so the next request usually
    goes through - which is what separates it from MomongaSkResponseNotExpected,
    where waiting is the right answer.

    Notifications never raise it. An unreadable property is handed back as raw
    bytes with a warning in the log.
    """


class MomongaResponseNotPossible(MomongaError):
    """The meter does not support a property that was asked for.

    One unsupported code in a request is enough, however many were sent. What
    the meter does support is in get_properties_to_get_values() and
    get_properties_to_set_values().
    """

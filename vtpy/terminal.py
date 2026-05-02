import serial  # type: ignore
import select
import sys
import time
from abc import ABC
from typing import List, Optional, Tuple, Union


class STDIOWrapper:
    def __init__(self, timeout: Optional[float] = 0.01) -> None:
        self.timeout = timeout

    def write(self, data: bytes) -> int:
        sys.stdout.buffer.write(data)
        return len(data)

    def read(self, size: int = 1) -> bytes:
        if self.timeout is not None:
            # First, select on stdin and wait the timeout seconds.
            rfds, _, _ = select.select([sys.stdin], [], [], self.timeout)
            if not rfds:
                return b""

        return sys.stdin.buffer.read(1)


class TerminalException(Exception):
    pass


class Terminal(ABC):
    ESCAPE: bytes = b"\x1B"

    G0_UK_CHARSET: bytes = b"(A"
    G0_US_CHARSET: bytes = b"(B"
    G0_SE_CHARSET: bytes = b"(H"
    G0_BOX_CHARSET: bytes = b"(0"
    G1_UK_CHARSET: bytes = b")A"
    G1_US_CHARSET: bytes = b")B"
    G1_SE_CHARSET: bytes = b")H"
    G1_BOX_CHARSET: bytes = b")0"

    REQUEST_STATUS: bytes = b"[5n"
    STATUS_OKAY: bytes = b"[0n"

    REQUEST_CURSOR: bytes = b"[6n"

    MOVE_CURSOR_ORIGIN: bytes = b"[H"
    MOVE_CURSOR_UP: bytes = b"M"
    MOVE_CURSOR_DOWN: bytes = b"D"

    CLEAR_TO_ORIGIN: bytes = b"[1J"
    CLEAR_TO_END_OF_LINE: bytes = b"[0K"
    CLEAR_SCREEN: bytes = b"[2J"
    CLEAR_LINE: bytes = b"[2K"

    SET_132_COLUMNS: bytes = b"[?3h"
    SET_80_COLUMNS: bytes = b"[?3l"

    TURN_ON_REGION: bytes = b"[?6h"
    TURN_OFF_REGION: bytes = b"[?6l"

    TURN_ON_AUTOWRAP: bytes = b"[?7h"
    TURN_OFF_AUTOWRAP: bytes = b"[?7l"

    TURN_ON_WRAP_FORWARD_MODE: bytes = b"[33h"
    TURN_OFF_WRAP_FORWARD_MODE: bytes = b"[33l"

    TURN_ON_WRAP_BACKWARD_MODE: bytes = b"[34h"
    TURN_OFF_WRAP_BACKWARD_MODE: bytes = b"[34l"

    SET_BOLD: bytes = b"[1m"
    SET_NORMAL: bytes = b"[0m"
    SET_UNDERLINE: bytes = b"[4m"
    SET_REVERSE: bytes = b"[7m"

    SAVE_CURSOR: bytes = b"7"
    RESTORE_CURSOR: bytes = b"8"

    DOUBLE_HEIGHT_TOP: bytes = b"#3"
    DOUBLE_HEIGHT_BOTTOM: bytes = b"#4"
    DOUBLE_WIDTH: bytes = b"#6"
    NORMAL_SIZE: bytes = b"#5"

    UP: bytes = b"[A"
    DOWN: bytes = b"[B"
    LEFT: bytes = b"[D"
    RIGHT: bytes = b"[C"
    BACKSPACE: bytes = b"\x08"
    DELETE: bytes = b"\x7F"

    CHECK_INTERVAL: float = 1.0
    MAX_FAILURES: int = 3

    def __init__(self, interface: Union[serial.Serial, STDIOWrapper]) -> None:
        self.interface = interface
        self.leftover = b""
        self.pending: List[bytes] = []
        self.responses: List[bytes] = []
        self.reversed = False
        self.bolded = False
        self.underlined = False
        self.autowrap = False
        self.boxMode = False
        self.lastPolled = time.time()
        self.pollFailures = 0

        # When saving/loading cursor position, the graphics rendering modes are
        # also saved/restored. These represnet the last bold, underline, reverse
        # and box mode settings.
        self.lastModes: Tuple[bool, bool, bool, bool] = (False, False, False, False)

        # First, connect and figure out what's going on.
        self.checkOk()

        # Reset terminal.
        self.columns: int = 80
        self.rows: int = 24
        self.cursor: Tuple[int, int] = (-1, -1)
        self.reset()

    def reset(self) -> None:
        self.sendCommand(self.SET_80_COLUMNS)
        self.sendCommand("[1;24r".encode("ascii"))
        self.sendCommand(self.TURN_OFF_REGION)
        self.sendCommand(self.CLEAR_SCREEN)
        self.sendCommand(self.MOVE_CURSOR_ORIGIN)
        self.sendCommand(self.SET_NORMAL)
        self.sendCommand(self.G0_SE_CHARSET)
        self.sendCommand(self.G1_SE_CHARSET)
        self.sendCommand(self.TURN_OFF_AUTOWRAP)
        self.interface.write(b"\x0F")
        self.boxMode = False

    def isOk(self) -> bool:
        self.sendCommand(self.REQUEST_STATUS)
        return self.recvResponse(1.0) == self.STATUS_OKAY

    def checkOk(self) -> None:
        if not self.isOk():
            raise TerminalException("Terminal did not respond okay!")

    def set132Columns(self) -> None:
        self.sendCommand(self.SET_132_COLUMNS)
        self.checkOk()

    def set80Columns(self) -> None:
        self.sendCommand(self.SET_80_COLUMNS)
        self.checkOk()

    def sendCommand(self, cmd: bytes) -> None:
        self.interface.write(self.ESCAPE)

        # Whether we've lost track of the cursor with this or not.
        reset = True

        if cmd == self.SET_NORMAL:
            self.reversed = False
            self.bolded = False
            self.underlined = False
            reset = False
        elif cmd == self.SET_REVERSE:
            self.reversed = True
            reset = False
        elif cmd == self.SET_BOLD:
            self.bolded = True
            reset = False
        elif cmd == self.SET_UNDERLINE:
            self.underlined = True
            reset = False
        elif cmd == self.SET_132_COLUMNS:
            self.columns = 132
        elif cmd == self.SET_80_COLUMNS:
            self.columns = 80
        elif cmd == self.TURN_OFF_AUTOWRAP:
            self.autowrap = False
            reset = False
        elif cmd == self.TURN_ON_AUTOWRAP:
            self.autowrap = True
            reset = False
        elif cmd in {
            self.G0_UK_CHARSET,
            self.G0_US_CHARSET,
            self.G0_SE_CHARSET,
            self.G0_BOX_CHARSET,
            self.G1_UK_CHARSET,
            self.G1_US_CHARSET,
            self.G0_SE_CHARSET,
            self.G1_BOX_CHARSET,
            self.TURN_ON_REGION,
            self.TURN_OFF_REGION,
            self.DOUBLE_HEIGHT_TOP,
            self.DOUBLE_HEIGHT_BOTTOM,
            self.DOUBLE_WIDTH,
            self.NORMAL_SIZE,
            self.REQUEST_STATUS,
            self.REQUEST_CURSOR,
        }:
            reset = False
        elif cmd == self.SAVE_CURSOR:
            reset = False
            self.lastMode = (self.bolded, self.underlined, self.reversed, self.boxMode)
        elif cmd == self.RESTORE_CURSOR:
            self.bolded, self.underlined, self.reversed, self.boxMode = self.lastMode

        if reset:
            # Force a full fetch next time we're asked for the cursor pos.
            self.cursor = (-1, -1)

        self.interface.write(cmd)

    def moveCursor(self, row: int, col: int) -> None:
        if row < 1 or row > self.rows:
            return
        if col < 1 or col > self.columns:
            return

        self.sendCommand(f"[{row};{col}H".encode("ascii"))
        self.cursor = (row, col)

    def fetchCursor(self) -> Tuple[int, int]:
        if self.cursor[0] != -1 and self.cursor[1] != -1:
            return self.cursor

        self.sendCommand(self.REQUEST_CURSOR)
        for _ in range(12):
            # We could be mid-page refresh, so give a wide berth.
            resp = self.recvResponse(0.25)
            if not resp:
                # Ran out of responses, try sending the command again.
                self.sendCommand(self.REQUEST_CURSOR)
            elif resp[:1] != b"[" or resp[-1:] != b"R":
                # Manual escape sequence sent by user? Swallow and read next.
                continue
            else:
                # Got a valid response!
                break
        else:
            raise TerminalException("Couldn't receive cursor position from terminal!")
        respstr = resp[1:-1].decode("ascii")
        row, col = respstr.split(";", 1)
        self.cursor = (int(row), int(col))
        return self.cursor

    def sendBytes(self, data: bytes) -> None:
        # Leave alternate character set mode before sending raw bytes.
        if self.boxMode:
            self.interface.write(b"\x0F")
            self.boxMode = False
    
        self.interface.write(data)
    
        row, col = self.cursor
    
        if row != -1 and col != -1:
            for value in data:
                if value in {10, 13}:  # LF or CR
                    row += 1
                    col = 1
                elif value < 32:
                    row = -1
                    col = -1
                    break
                else:
                    col += 1
                    if col > self.columns:
                        if self.autowrap:
                            col = 1
                            row += 1
                        else:
                            col = self.columns
    
                    if row > self.rows:
                        row = -1
                        col = -1
                        break
    
        self.cursor = (row, col)
    
    def sendText(self, text: str) -> None:
        row, col = self.cursor
        inAlt = self.boxMode

        def alt(char: bytes) -> bytes:
            nonlocal inAlt

            add = False
            if not inAlt:
                inAlt = True
                add = True

            return (b"\x0E" if add else b"") + char

        def norm(char: bytes) -> bytes:
            nonlocal inAlt

            add = False
            if inAlt:
                inAlt = False
                add = True

            return (b"\x0F" if add else b"") + char

        def fb(data: str) -> bytes:
            nonlocal row
            nonlocal col

            if row != -1 and col != -1:
                # Try to calculate where the cursor will be after this.
                if data in {"\r", "\n"}:
                    row += 1
                    col = 1
                elif ord(data) < 32:
                    row = -1
                    col = -1
                elif data == "\t":
                    row = -1
                    col = -1
                else:
                    col += 1
                    if col > self.columns:
                        if self.autowrap:
                            col = 1
                            row += 1
                        else:
                            col = self.columns

                if row > self.rows:
                    row = -1
                    col = -1

            try:
                return norm(data.encode("ascii"))
            except UnicodeEncodeError:
                # Box drawing mappings to VT-100
                if data == "\u2500":
                    return alt(b"\x71")
                if data == "\u2502":
                    return alt(b"\x78")
                if data == "\u250c":
                    return alt(b"\x6C")
                if data == "\u2510":
                    return alt(b"\x6B")
                if data == "\u2514":
                    return alt(b"\x6D")
                if data == "\u2518":
                    return alt(b"\x6A")
                if data == "\u253c":
                    return alt(b"\x6e")
                if data == "\u251c":
                    return alt(b"\x74")
                if data == "\u2524":
                    return alt(b"\x75")
                if data == "\u2534":
                    return alt(b"\x76")
                if data == "\u252c":
                    return alt(b"\x77")

                # Accented character mappings to VT-100 non-accented standard characters.
                if data in {"\u00c0", "\u00c1", "\u00c2", "\u00c3", "\u00c4", "\u00c5"}:
                    return norm(b"A")
                if data == "\u00c7":
                    return norm(b"C")
                if data in {"\u00c8", "\u00c9", "\u00ca", "\u00cb"}:
                    return norm(b"E")
                if data in {"\u00cc", "\u00cd", "\u00ce", "\u00cf"}:
                    return norm(b"I")
                if data == "\u00d0":
                    return norm(b"D")
                if data == "\u00d1":
                    return norm(b"N")
                if data in {"\u00d2", "\u00d3", "\u00d4", "\u00d5", "\u00d6"}:
                    return norm(b"O")
                if data in {"\u00d9", "\u00da", "\u00db", "\u00dc"}:
                    return norm(b"U")
                if data == "\u00dd":
                    return norm(b"Y")

                if data in {"\u00e0", "\u00e1", "\u00e2", "\u00e3", "\u00e4", "\u00e5"}:
                    return norm(b"a")
                if data == "\u00e7":
                    return norm(b"c")
                if data in {"\u00e8", "\u00e9", "\u00ea", "\u00eb"}:
                    return norm(b"e")
                if data in {"\u00ec", "\u00ed", "\u00ee", "\u00ef"}:
                    return norm(b"i")
                if data == "\u00f0":
                    return norm(b"o")
                if data == "\u00f1":
                    return norm(b"n")
                if data in {"\u00f2", "\u00f3", "\u00f4", "\u00f5", "\u00f6"}:
                    return norm(b"o")
                if data in {"\u00f9", "\u00fa", "\u00fb", "\u00fc"}:
                    return norm(b"u")
                if data in {"\u00fd", "\u00ff"}:
                    return norm(b"y")

                # Fill-drawing mapping hacks.
                if data == "\u2591":
                    if not self.bolded:
                        # We can just display.
                        return alt(b"\x6E")
                    else:
                        # We must un-bold for this special drawing character. Then, we must re-bold,
                        # and possibly re-reverse if that was what was going on.
                        return alt(
                            self.ESCAPE
                            + self.SET_NORMAL
                            + (
                                (self.ESCAPE + self.SET_REVERSE)
                                if self.reversed
                                else b""
                            )
                            + (
                                (self.ESCAPE + self.SET_UNDERLINE)
                                if self.underlined
                                else b""
                            )
                            + b"\x6E"
                            + self.ESCAPE
                            + self.SET_BOLD
                        )
                if data == "\u2592":
                    if not self.bolded:
                        # We can just display.
                        return alt(b"\x61")
                    else:
                        # We must un-bold for this special drawing character. Then, we must re-bold,
                        # and possibly re-reverse if that was what was going on.
                        return alt(
                            self.ESCAPE
                            + self.SET_NORMAL
                            + (
                                (self.ESCAPE + self.SET_REVERSE)
                                if self.reversed
                                else b""
                            )
                            + (
                                (self.ESCAPE + self.SET_UNDERLINE)
                                if self.underlined
                                else b""
                            )
                            + b"\x61"
                            + self.ESCAPE
                            + self.SET_BOLD
                        )
                if data == "\u2593":
                    if self.bolded:
                        # We can just display.
                        return alt(b"\x61")
                    else:
                        # We must bold this for the special drawing character, then un-bold it once
                        # we're done, and possible add re-reversing.
                        return alt(
                            self.ESCAPE
                            + self.SET_BOLD
                            + b"\x61"
                            + self.ESCAPE
                            + self.SET_NORMAL
                            + (
                                (self.ESCAPE + self.SET_REVERSE)
                                if self.reversed
                                else b""
                            )
                            + (
                                (self.ESCAPE + self.SET_UNDERLINE)
                                if self.underlined
                                else b""
                            )
                        )
                if data == "\u2588":
                    return norm(
                        self.ESCAPE
                        + (self.SET_NORMAL if self.reversed else self.SET_REVERSE)
                        + b" "
                        + self.ESCAPE
                        + (self.SET_REVERSE if self.reversed else self.SET_NORMAL)
                        + ((self.ESCAPE + self.SET_BOLD) if self.bolded else b"")
                        + (
                            (self.ESCAPE + self.SET_UNDERLINE)
                            if self.underlined
                            else b""
                        )
                    )

                # Degrees symbol.
                if data == "\xb0":
                    return alt(b"\x66")
                # +/- combined.
                if data == "\xb1":
                    return alt(b"\x67")
                # Less than or equal
                if data == "\u2264":
                    return alt(b"\x79")
                # Greater than or equal.
                if data == "\u2265":
                    return alt(b"\x7a")
                # Pi.
                if data == "\u03c0":
                    return alt(b"\x7b")
                # Not equal to symbol.
                if data == "\u2260":
                    return alt(b"\x7c")
                # Pound symbol.
                if data == "\u00a3":
                    return alt(b"\x7d")
                # Moddle dot.
                if data in {"\u00b7", "\u2022"}:
                    return alt(b"\x7e")
                # Alternate single quotation marks.
                if data in {"\u2018", "\u2019", "\u201a", "\u201b", "\u2032", "\u2035"}:
                    return norm(b"'")
                # Alternate double quotation marks.
                if data in {"\u201c", "\u201d", "\u201e", "\u201f", "\u2033", "\u2036"}:
                    return norm(b'"')
                # Alternate asterisks.
                if data in {"\u204e", "\u2055"}:
                    return norm(b"*")
                # Alternate semicolon.
                if data == "\u204f":
                    return norm(b";")
                # Alternate percent.
                if data == "\u2052":
                    return norm(b"%")
                # Alternate tilde.
                if data == "\u2053":
                    return norm(b"~")

                # Unknown unicode.
                return alt(b"\x60")

        self.interface.write(b"".join(fb(s) for s in text))

        self.boxMode = inAlt

        if row == -1 or col == -1:
            self.cursor = (-1, -1)
        else:
            self.cursor = (row, col)

    def setAutoWrap(self, value: bool = True) -> None:
        if (not self.autowrap) and value:
            self.sendCommand(self.TURN_ON_AUTOWRAP)
        elif self.autowrap and (not value):
            self.sendCommand(self.TURN_OFF_AUTOWRAP)

    def clearAutoWrap(self) -> None:
        self.setAutoWrap(False)

    def setScrollRegion(self, top: int, bottom: int) -> None:
        self.sendCommand(f"[{top};{bottom}r".encode("ascii"))
        self.sendCommand(self.TURN_ON_REGION)

    def clearScrollRegion(self) -> None:
        self.sendCommand(self.TURN_OFF_REGION)

    def recvResponse(self, timeout: Optional[float] = None) -> bytes:
        # Fetch the last received response in the input loop, or if that is empty,
        # attempt to read the next response from the serial terminal.
        if self.responses:
            response = self.responses[0]
            self.responses = self.responses[1:]
        else:
            response = self._recvResponse(timeout)
        return response

    def _recvResponse(self, timeout: Optional[float]) -> bytes:
        # Attempt to read the next response from the serial terminal, handling escaped
        # arrowkeys as inputs as apposed to command responses.
        while True:
            oldInputLen = len(self.pending)
            resp = self._recvResponseImpl(timeout)
            if resp or len(self.pending) > oldInputLen:
                # We got a successful response of some type, reset our polling.
                self.lastPolled = time.time()
            if resp in {self.UP, self.DOWN, self.LEFT, self.RIGHT}:
                self.pending.append(resp)
            else:
                return resp

    def _recvResponseImpl(self, timeout: Optional[float]) -> bytes:
        # Attempt to read from serial until we have a valid escaped response. All non
        # escaped responses will be placed into the user input buffer.
        gotResponse: bool = False
        accum: bytes = b""

        start = time.time()
        while True:
            # Grab extra command bits from previous call to recvResponse first, then
            # grab characters from the device itself.
            if self.leftover:
                val = self.leftover[0:1]
                self.leftover = self.leftover[1:]
            else:
                val = self.interface.read()

            if not val:
                if gotResponse or (timeout and (time.time() - start) > timeout):
                    # Got a full command here.
                    while accum and (accum[0:1] != self.ESCAPE):
                        self.pending.append(accum[0:1])
                        accum = accum[1:]

                    if accum and accum[0:1] == self.ESCAPE:
                        # We could have some regular input after this. So parse the command a little.
                        accum = accum[1:]

                        for offs in range(len(accum)):
                            val = accum[offs : (offs + 1)]
                            if val not in {
                                b"0",
                                b"1",
                                b"2",
                                b"3",
                                b"4",
                                b"5",
                                b"6",
                                b"7",
                                b"8",
                                b"9",
                                b";",
                                b"?",
                                b"[",
                            }:
                                # This is the last character, so everything after is going to
                                # end up being the next response or some user input.
                                # Add the rest of the leftovers to be processed next time.
                                self.leftover += accum[offs + 1 :]
                                return accum[: (offs + 1)]

                        # This can happen if the user presses the "ESC" key which sends the escape
                        # sequence raw with nothing else available. It can also happen if the terminal
                        # sends responses too slow to us and we've partially accumulated a value.
                        # Requeue this escape key and the rest of the accum and hope the user presses
                        # something else next.
                        self.leftover += self.ESCAPE + accum
                        accum = b""
                    else:
                        accum = b""
                        if timeout:
                            return b""
                        else:
                            gotResponse = False

                continue

            gotResponse = True
            accum += val

    def peekInput(self) -> Optional[bytes]:
        # Simply return the next input, or None if there is nothing pending.
        if self.pending:
            return self.pending[0]

        return None

    def recvInput(self) -> Optional[bytes]:
        # Pump response queue to grab input between any escaped values. Skip
        # that if we already have pending input since we don't need a round-trip.
        if not self.pending:
            response = self._recvResponse(timeout=0.01)
            if response:
                self.responses.append(response)

        # Also, occasionally check that the terminal is still alive.
        now = time.time()
        if now - self.lastPolled > self.CHECK_INTERVAL:
            self.lastPolled = now
            if self.isOk():
                self.pollFailures = 0
            else:
                self.pollFailures += 1
                if self.pollFailures > self.MAX_FAILURES:
                    # Do a hard check instead of soft.
                    self.checkOk()

        # See if we have anything pending.
        val: Optional[bytes] = None
        if self.pending:
            val = self.pending[0]
            self.pending = self.pending[1:]
        return val


class SerialTerminal(Terminal):
    def __init__(self, port: str, baud: int, flowControl: bool = False) -> None:
        super().__init__(serial.Serial(port, baud, xonxoff=flowControl, timeout=0.01))


class STDIOTerminal(Terminal):
    def __init__(self) -> None:
        super().__init__(STDIOWrapper(timeout=0.01))

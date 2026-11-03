"""The checker's serial log: lines in, commands out.

Each line from the device is "<seconds>.<milliseconds> <event> [word ...]",
where words are key=value fields or plain words (see firmware/README.md).
"""

import os
import select
import time

BAUD = 115200


class Line:
    """One parsed log line."""

    def __init__(self, text):
        self.text = text
        parts = text.split()
        self.time = None
        if parts:
            try:
                self.time = float(parts[0])
                parts = parts[1:]
            except ValueError:
                pass
        self.event = parts[0] if parts else ""
        self.words = parts[1:]
        self.fields = {}
        for w in self.words:
            key, sep, value = w.partition("=")
            if sep:
                self.fields[key] = value

    def __repr__(self):
        return f"Line({self.text!r})"


class Port:
    """A serial port at 115200 8N1, read line by line.

    Uses termios on Linux and macOS, and pyserial where termios is missing
    (Windows).
    """

    def __init__(self, path):
        self._buf = b""
        try:
            import termios  # noqa: F401
            self._open_posix(path)
        except ImportError:
            self._open_pyserial(path)

    def _open_posix(self, path):
        import termios
        import tty
        self._fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(self._fd)
        attrs = termios.tcgetattr(self._fd)
        attrs[4] = attrs[5] = termios.B115200
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
        self._serial = None

    def _open_pyserial(self, path):
        import serial
        self._serial = serial.Serial(path, BAUD, timeout=0)
        self._fd = None

    def close(self):
        if self._serial is not None:
            self._serial.close()
        else:
            os.close(self._fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self, timeout):
        if self._serial is not None:
            self._serial.timeout = timeout
            return self._serial.read(4096)
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return b""
        try:
            return os.read(self._fd, 4096)
        except BlockingIOError:
            return b""

    def send(self, command):
        """Send one command line. A leading newline ends any partial line."""
        data = ("\n" + command + "\n").encode("ascii")
        if self._serial is not None:
            self._serial.write(data)
        else:
            os.write(self._fd, data)

    def readline(self, timeout):
        """The next line, or None after `timeout` seconds without one."""
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self._buf += self._read(left)
        raw, _, self._buf = self._buf.partition(b"\n")
        return Line(raw.decode("ascii", "replace").strip("\r"))

    def lines(self, timeout):
        """Lines until `timeout` seconds pass without one."""
        while True:
            line = self.readline(timeout)
            if line is None:
                return
            yield line

    def command(self, command, event, timeout=2.0):
        """Send `command` and return the first `event` line after it."""
        self.send(command)
        deadline = time.monotonic() + timeout
        while True:
            line = self.readline(max(0.0, deadline - time.monotonic()))
            if line is None:
                return None
            if line.event in (event, "error"):
                return line

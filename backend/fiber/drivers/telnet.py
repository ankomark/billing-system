"""
Just enough telnet to log in to an OLT's CLI, run show commands, and leave.

telnetlib left the standard library in Python 3.13. What an OLT needs is
small: refuse every option the far end proposes, answer the login and
password prompts, page through "--More--", and read until the prompt comes
back. Nothing here sends a configuration command; the drivers only ever pass
`show ...` lines, and `run` refuses anything else.
"""

import re
import socket
import time

from .base import OltError

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240

LOGIN_RE = re.compile(rb"(user ?name|login)\s*:\s*$", re.I)
PASSWORD_RE = re.compile(rb"password\s*:\s*$", re.I)
PROMPT_RE = re.compile(rb"[\w.\-()/ ]{1,40}[>#]\s*$")
MORE_RE = re.compile(rb"--+\s*more\s*--+|press any key|\(q\)uit", re.I)
FAIL_RE = re.compile(rb"(login incorrect|authentication failed|bad password|access denied)", re.I)

# Read-only verbs a driver may send. Anything else is refused before it
# reaches the wire.
ALLOWED = re.compile(r"^\s*(show|display|enable|terminal length 0|screen-length 0 temporary)\b", re.I)


def strip_telnet(data, sock):
    """Remove IAC sequences from `data`, refusing every option asked of us."""
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b != IAC:
            out.append(b)
            i += 1
            continue
        if i + 1 >= len(data):
            break
        cmd = data[i + 1]
        if cmd in (DO, DONT, WILL, WONT) and i + 2 < len(data):
            opt = data[i + 2]
            if sock is not None and cmd in (DO, WILL):
                sock.sendall(bytes([IAC, WONT if cmd == DO else DONT, opt]))
            i += 3
        elif cmd == SB:
            end = data.find(bytes([IAC, SE]), i)
            i = len(data) if end < 0 else end + 2
        elif cmd == IAC:
            out.append(IAC)
            i += 2
        else:
            i += 2
    return bytes(out)


class TelnetCli:
    def __init__(self, host, port, username, password, timeout=8.0):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.timeout = timeout
        self.sock = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def _recv_until(self, patterns, deadline):
        buf = b""
        while time.monotonic() < deadline:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                raise OltError("OLT closed the CLI connection")
            buf += strip_telnet(chunk, self.sock)
            tail = buf[-200:]
            if MORE_RE.search(tail):
                buf = MORE_RE.sub(b"", buf)
                self.sock.sendall(b" ")
                continue
            for name, pat in patterns:
                if pat.search(tail):
                    return name, buf
        raise OltError("timed out waiting for the OLT CLI")

    def open(self):
        if not self.username:
            raise OltError("no CLI username set for this OLT")
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise OltError(f"CLI connect failed: {exc}") from exc
        self.sock.settimeout(1.0)
        deadline = time.monotonic() + self.timeout
        what, _ = self._recv_until([("login", LOGIN_RE), ("prompt", PROMPT_RE)], deadline)
        if what == "login":
            self.sock.sendall(self.username.encode() + b"\r\n")
            self._recv_until([("password", PASSWORD_RE)], deadline)
            self.sock.sendall((self.password or "").encode() + b"\r\n")
            what, buf = self._recv_until(
                [("fail", FAIL_RE), ("login", LOGIN_RE), ("prompt", PROMPT_RE)], deadline)
            if what != "prompt":
                raise OltError("CLI login refused")

    def run(self, command):
        if not ALLOWED.match(command):
            raise OltError(f"refusing to send a non-read command: {command!r}")
        self.sock.sendall(command.encode() + b"\r\n")
        _, buf = self._recv_until([("prompt", PROMPT_RE)], time.monotonic() + self.timeout)
        text = buf.decode("utf-8", "replace").replace("\r", "")
        lines = text.split("\n")
        # Drop the echoed command and the trailing prompt.
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        if lines and PROMPT_RE.search(lines[-1].encode()):
            lines = lines[:-1]
        return "\n".join(lines)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.sendall(b"exit\r\n")
            except OSError:
                pass
            self.sock.close()
            self.sock = None

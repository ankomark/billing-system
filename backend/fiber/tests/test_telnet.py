"""The telnet CLI client against a fake OLT shell: options, login, paging, read-only guard."""

import socket
import threading

from django.test import SimpleTestCase

from fiber.drivers.base import OltError
from fiber.drivers.telnet import DO, IAC, WILL, TelnetCli, strip_telnet

TABLE = ("OnuId  MAC  Status  Rx Power(dBm)\r\n" + "".join(
    f"EPON0/1:{i}  E0:67:01:00:00:{i:02X}  online  -2{i}.5\r\n" for i in range(1, 4)))


class FakeShell:
    def __init__(self, password="admin"):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.password = password
        self.received = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _line(self, conn):
        buf = b""
        while not buf.endswith(b"\n"):
            ch = conn.recv(1)
            if not ch:
                return None
            buf += ch
        return buf.strip().decode()

    def _serve(self):
        conn, _ = self.srv.accept()
        conn.sendall(bytes([IAC, DO, 1, IAC, WILL, 3]) + b"Welcome\r\nUsername: ")
        conn.recv(3)
        conn.recv(3)
        user = self._line(conn)
        conn.sendall(b"Password: ")
        pw = self._line(conn)
        if pw != self.password:
            conn.sendall(b"\r\nLogin incorrect\r\nUsername: ")
            conn.close()
            return
        conn.sendall(b"\r\nOLT> ")
        while True:
            cmd = self._line(conn)
            if cmd is None:
                break
            self.received.append(cmd)
            if cmd == "exit":
                break
            if cmd.startswith("show onu"):
                half = TABLE.split("\r\n")
                conn.sendall(cmd.encode() + b"\r\n" + "\r\n".join(half[:2]).encode() + b"\r\n --More-- ")
                conn.recv(1)
                conn.sendall("\r\n".join(half[2:]).encode() + b"OLT> ")
            else:
                conn.sendall(cmd.encode() + b"\r\nOLT> ")
        conn.close()
        self.srv.close()
        _ = user


class TelnetTests(SimpleTestCase):
    def test_login_page_through_more_and_read_a_table(self):
        shell = FakeShell()
        with TelnetCli("127.0.0.1", shell.port, "admin", "admin", timeout=5) as t:
            out = t.run("show onu info epon 0/1")
        self.assertIn("EPON0/1:3", out)
        self.assertNotIn("More", out)

    def test_wrong_password_is_an_olt_error(self):
        shell = FakeShell(password="right")
        with self.assertRaises(OltError):
            with TelnetCli("127.0.0.1", shell.port, "admin", "wrong", timeout=3):
                pass

    def test_refuses_anything_but_read_commands(self):
        shell = FakeShell()
        with TelnetCli("127.0.0.1", shell.port, "admin", "admin", timeout=5) as t:
            for bad in ("reboot", "config", "onu delete 0/1 1", "write"):
                with self.assertRaises(OltError):
                    t.run(bad)
        self.assertFalse(any(c.startswith(("reboot", "config", "onu", "write")) for c in shell.received))

    def test_options_are_refused_and_stripped(self):
        sent = []

        class Sock:
            def sendall(self, b):
                sent.append(b)

        clean = strip_telnet(bytes([IAC, DO, 24]) + b"hello" + bytes([IAC, WILL, 1]), Sock())
        self.assertEqual(clean, b"hello")
        self.assertEqual(sent, [bytes([IAC, 252, 24]), bytes([IAC, 254, 1])])

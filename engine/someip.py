"""Minimal SOME/IP wire-protocol client (no device or Robot Framework dependency).

Implements the 16-byte SOME/IP header (message id, length, request id, protocol/interface version,
message type, return code) and a UDP/TCP request-response exchange, so a test can talk to a
service offered by a COVESA vsomeip application on the ECU. SOME/IP-SD (service discovery) is not
implemented: the ECU's unicast address, service id and method id are given explicitly.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

HEADER = struct.Struct(">HHIHHBBBB")   # service, method, length, client, session, proto, iface, type, rc
PROTOCOL_VERSION = 0x01

REQUEST, REQUEST_NO_RETURN, NOTIFICATION, RESPONSE, ERROR = 0x00, 0x01, 0x02, 0x80, 0x81
RETURN_CODES = {0x00: "E_OK", 0x01: "E_NOT_OK", 0x02: "E_UNKNOWN_SERVICE", 0x03: "E_UNKNOWN_METHOD",
                0x04: "E_NOT_READY", 0x05: "E_NOT_REACHABLE", 0x06: "E_TIMEOUT",
                0x07: "E_WRONG_PROTOCOL_VERSION", 0x08: "E_WRONG_INTERFACE_VERSION",
                0x09: "E_MALFORMED_MESSAGE", 0x0A: "E_WRONG_MESSAGE_TYPE"}


@dataclass
class Message:
    service: int
    method: int
    client: int = 0x0001
    session: int = 0x0001
    interface_version: int = 0x01
    message_type: int = REQUEST
    return_code: int = 0x00
    payload: bytes = b""

    def pack(self) -> bytes:
        # The length field counts everything after itself: 8 header bytes + payload.
        return HEADER.pack(self.service, self.method, 8 + len(self.payload), self.client, self.session,
                           PROTOCOL_VERSION, self.interface_version, self.message_type,
                           self.return_code) + self.payload

    @classmethod
    def unpack(cls, data: bytes) -> "Message":
        if len(data) < HEADER.size:
            raise ValueError(f"SOME/IP message too short: {len(data)} bytes")
        service, method, length, client, session, _proto, iface, mtype, rc = HEADER.unpack_from(data)
        payload = data[HEADER.size:HEADER.size + length - 8]
        if len(payload) != length - 8:
            raise ValueError(f"SOME/IP length field says {length - 8} payload bytes, got {len(payload)}")
        return cls(service, method, client, session, iface, mtype, rc, payload)

    @property
    def return_code_name(self) -> str:
        return RETURN_CODES.get(self.return_code, f"0x{self.return_code:02X}")


def to_int(value: int | str) -> int:
    return int(value, 0) if isinstance(value, str) else int(value)


def to_bytes(value: bytes | str) -> bytes:
    """Payload from bytes or a hex string such as '01 02 0A' / '0x01020A'."""
    if isinstance(value, bytes):
        return value
    cleaned = value.replace("0x", "").replace(" ", "").replace(":", "")
    return bytes.fromhex(cleaned)


class SomeIpClient:
    def __init__(self, host: str, port: int, protocol: str = "udp", timeout: float = 3.0,
                 client_id: int = 0x0001):
        if protocol.lower() not in ("udp", "tcp"):
            raise ValueError("protocol must be 'udp' or 'tcp'")
        self.addr, self.protocol, self.timeout, self.client_id = (host, port), protocol.lower(), timeout, client_id
        self._session = 0
        kind = socket.SOCK_DGRAM if self.protocol == "udp" else socket.SOCK_STREAM
        self.sock = socket.socket(socket.AF_INET, kind)
        self.sock.settimeout(timeout)
        if self.protocol == "tcp":
            self.sock.connect(self.addr)

    def _next_session(self) -> int:
        self._session = self._session % 0xFFFF + 1     # 0 is reserved for "session handling off"
        return self._session

    def _send(self, msg: Message) -> None:
        data = msg.pack()
        if self.protocol == "udp":
            self.sock.sendto(data, self.addr)
        else:
            self.sock.sendall(data)

    def _recv(self) -> Message:
        if self.protocol == "udp":
            return Message.unpack(self.sock.recvfrom(65535)[0])
        head = self._recv_exact(HEADER.size)
        length = struct.unpack_from(">I", head, 4)[0]
        return Message.unpack(head + self._recv_exact(length - 8))

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("SOME/IP peer closed the connection")
            buf += chunk
        return buf

    def request(self, service: int, method: int, payload: bytes = b"", interface_version: int = 1) -> Message:
        """Send a REQUEST and return the matching RESPONSE/ERROR (matched on request id)."""
        session = self._next_session()
        self._send(Message(service, method, self.client_id, session, interface_version, REQUEST, 0, payload))
        while True:
            try:
                reply = self._recv()
            except socket.timeout:
                raise TimeoutError(f"No SOME/IP response from {self.addr[0]}:{self.addr[1]} "
                                   f"for service 0x{service:04X} method 0x{method:04X}") from None
            if (reply.service, reply.method, reply.session) == (service, method, session) \
                    and reply.message_type in (RESPONSE, ERROR):
                return reply

    def fire_and_forget(self, service: int, method: int, payload: bytes = b"", interface_version: int = 1) -> None:
        self._send(Message(service, method, self.client_id, self._next_session(), interface_version,
                           REQUEST_NO_RETURN, 0, payload))

    def wait_for_notification(self, service: int, event: int) -> Message:
        """Block until a NOTIFICATION for the given service/event id (e.g. 0x8001) arrives."""
        while True:
            try:
                msg = self._recv()
            except socket.timeout:
                raise TimeoutError(f"No notification for service 0x{service:04X} event 0x{event:04X}") from None
            if (msg.service, msg.method, msg.message_type) == (service, event, NOTIFICATION):
                return msg

    def close(self) -> None:
        self.sock.close()

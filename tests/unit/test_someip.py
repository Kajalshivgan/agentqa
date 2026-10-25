import socket
import threading

import pytest

from engine.someip import ERROR, NOTIFICATION, RESPONSE, Message, SomeIpClient, to_bytes
from engine.SomeIpLibrary import SomeIpLibrary


def test_pack_layout_and_roundtrip():
    msg = Message(0x1234, 0x0001, client=0x0A0B, session=0x0C0D, payload=b"\x01\x02\x03")
    raw = msg.pack()
    assert raw[:8] == bytes.fromhex("1234 0001 0000000B".replace(" ", ""))  # length = 8 + 3
    assert raw[8:16] == bytes.fromhex("0A0B0C0D01010000")
    assert Message.unpack(raw) == msg


def test_unpack_rejects_bad_length():
    raw = Message(1, 2, payload=b"abc").pack()[:-1]
    with pytest.raises(ValueError):
        Message.unpack(raw)


def test_to_bytes_accepts_hex_styles():
    assert to_bytes("01 02 0A") == b"\x01\x02\x0a"
    assert to_bytes("0x0102") == b"\x01\x02"
    assert to_bytes("") == b""


def _udp_ecu(handler):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))

    def serve():
        data, addr = sock.recvfrom(65535)
        for out in handler(Message.unpack(data)):
            sock.sendto(out.pack(), addr)
    threading.Thread(target=serve, daemon=True).start()
    return sock


def _echo_response(req):
    return [Message(req.service, req.method, req.client, req.session, message_type=RESPONSE,
                    payload=req.payload[::-1])]


def test_request_response_over_udp():
    ecu = _udp_ecu(_echo_response)
    client = SomeIpClient("127.0.0.1", ecu.getsockname()[1], timeout=2)
    reply = client.request(0x1234, 0x0001, b"\x01\x02")
    assert reply.payload == b"\x02\x01" and reply.return_code_name == "E_OK"
    client.close()


def test_unrelated_notification_is_skipped_before_response():
    def handler(req):
        return [Message(req.service, 0x8001, message_type=NOTIFICATION, payload=b"x"), *_echo_response(req)]
    ecu = _udp_ecu(handler)
    client = SomeIpClient("127.0.0.1", ecu.getsockname()[1], timeout=2)
    assert client.request(0x1234, 0x0001, b"\x07").payload == b"\x07"


def test_error_response_exposes_return_code():
    ecu = _udp_ecu(lambda r: [Message(r.service, r.method, r.client, r.session, message_type=ERROR, return_code=0x03)])
    client = SomeIpClient("127.0.0.1", ecu.getsockname()[1], timeout=2)
    assert client.request(0x1234, 0x0099).return_code_name == "E_UNKNOWN_METHOD"


def test_timeout_when_ecu_silent():
    ecu = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ecu.bind(("127.0.0.1", 0))
    client = SomeIpClient("127.0.0.1", ecu.getsockname()[1], timeout=0.2)
    with pytest.raises(TimeoutError):
        client.request(0x1234, 0x0001)


def test_robot_library_expect_keyword():
    ecu = _udp_ecu(_echo_response)
    lib = SomeIpLibrary(timeout=2)
    lib.connect("127.0.0.1", ecu.getsockname()[1])
    lib.send_request_and_expect("0x1234", "0x0001", expected_payload="03 02 01", payload="01 02 03")
    lib.disconnect()


def test_library_requires_connection():
    with pytest.raises(RuntimeError):
        SomeIpLibrary().send_request("0x1", "0x1")

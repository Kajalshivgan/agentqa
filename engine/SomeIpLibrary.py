"""Robot Framework keywords for SOME/IP, so one test can drive both the HMI and the ECU.

    Library    engine.SomeIpLibrary

    *** Test Cases ***
    Set Temperature On ECU And Check HMI
        Connect To SomeIP Service    192.168.1.50    30509
        ${resp}=    Send SomeIP Request    0x1234    0x0001    payload=00 16
        Should Be Equal    ${resp.return_code_name}    E_OK
        Tap On Text    Climate
        Assert Visible    22
"""

from __future__ import annotations

from robot.api import logger
from robot.api.deco import keyword, library

from engine.someip import Message, SomeIpClient, to_bytes, to_int


@library(scope="SUITE", auto_keywords=False)
class SomeIpLibrary:
    def __init__(self, timeout: float = 3.0):
        self._timeout = float(timeout)
        self._client: SomeIpClient | None = None

    def _need(self) -> SomeIpClient:
        if self._client is None:
            raise RuntimeError("Not connected: call 'Connect To SomeIP Service' first")
        return self._client

    @keyword("Connect To SomeIP Service")
    def connect(self, host: str, port: int, protocol: str = "udp", client_id: str = "0x0001"):
        """Open a UDP (default) or TCP channel to the ECU's SOME/IP endpoint."""
        self.disconnect()
        self._client = SomeIpClient(host, int(port), protocol, self._timeout, to_int(client_id))
        logger.info(f"SOME/IP {protocol} -> {host}:{port}")

    @keyword("Disconnect From SomeIP Service")
    def disconnect(self):
        if self._client:
            self._client.close()
            self._client = None

    @keyword("Send SomeIP Request")
    def send_request(self, service: str, method: str, payload: str = "", interface_version: str = "1") -> Message:
        """Send a REQUEST and return the response. IDs accept decimal or 0x hex; payload is hex."""
        reply = self._need().request(to_int(service), to_int(method), to_bytes(payload), to_int(interface_version))
        logger.info(f"response {reply.return_code_name}, payload={reply.payload.hex(' ')}")
        return reply

    @keyword("Send SomeIP Request And Expect")
    def send_request_and_expect(self, service: str, method: str, expected_payload: str, payload: str = ""):
        """Send a REQUEST and fail unless the response is E_OK with exactly `expected_payload`."""
        reply = self.send_request(service, method, payload)
        if reply.return_code != 0:
            raise AssertionError(f"SOME/IP returned {reply.return_code_name}")
        if reply.payload != to_bytes(expected_payload):
            raise AssertionError(f"Payload {reply.payload.hex(' ')} != expected {to_bytes(expected_payload).hex(' ')}")

    @keyword("Send SomeIP Fire And Forget")
    def fire_and_forget(self, service: str, method: str, payload: str = ""):
        """Send a REQUEST_NO_RETURN message (no response expected)."""
        self._need().fire_and_forget(to_int(service), to_int(method), to_bytes(payload))

    @keyword("Wait For SomeIP Notification")
    def wait_for_notification(self, service: str, event: str) -> Message:
        """Block until the ECU sends a NOTIFICATION for the given service and event id."""
        return self._need().wait_for_notification(to_int(service), to_int(event))

    @keyword("SomeIP Payload Should Be")
    def payload_should_be(self, message: Message, expected: str):
        if message.payload != to_bytes(expected):
            raise AssertionError(f"Payload {message.payload.hex(' ')} != expected {to_bytes(expected).hex(' ')}")

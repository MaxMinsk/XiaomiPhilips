"""Never send discovery or commands to a real lamp from automated tests."""

import pytest
from miio.miioprotocol import MiIOProtocol


@pytest.fixture(autouse=True)
def forbid_lamp_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Mock the lamp transport before accessing the network")

    monkeypatch.setattr(MiIOProtocol, "send_handshake", fail)

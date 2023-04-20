#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""EC CHANGESTATE methods with one or more event IDs"""

import pytest

from tests.testlib import CMKEventConsole

from tests.unit.cmk.ec.helpers import FakeStatusSocket

from cmk.ec.main import Event, EventStatus, StatusServer
from cmk.ec.query import MKClientError


def test_changestate_of_nonexistent_event(status_server: StatusServer) -> None:
    """Exception on nonexistent event ID"""

    s = FakeStatusSocket(b"COMMAND CHANGESTATE;1;testuser;2")

    with pytest.raises(MKClientError) as excinfo:
        status_server.handle_client(s, True, "127.0.0.1")

    assert "No event with id 1" in str(excinfo.value)


def test_change_event_state(event_status: EventStatus, status_server: StatusServer) -> None:
    """Changestate 1 event."""
    event: Event = {
        "host": "ABC1",
        "text": "not important",
        "core_host": "ABC",
    }
    event_status.new_event(CMKEventConsole.new_event(event))
    assert len(event_status.events()) == 1

    s = FakeStatusSocket(b"COMMAND CHANGESTATE;1;testuser;2")
    status_server.handle_client(s, True, "127.0.0.1")

    assert event_status.events()[0]["state"] == 2


def test_changetestate_of_multiple_events(
    event_status: EventStatus, status_server: StatusServer
) -> None:
    """Changestate event list."""
    events: list[Event] = [
        {
            "host": "ABC1",
            "text": "event1 text",
            "core_host": "ABC",
        },
        {
            "host": "ABC2",
            "text": "event2 text",
            "core_host": "ABC",
        },
    ]
    for event in events:
        event_status.new_event(CMKEventConsole.new_event(event))

    assert len(event_status.events()) == 2

    s = FakeStatusSocket(b"COMMAND CHANGESTATE;1,2;testuser;2")
    status_server.handle_client(s, True, "127.0.0.1")

    assert event_status.events()[0]["state"] == 2
    assert event_status.events()[1]["state"] == 2


def test_changestate_of_partially_existing_multiple_events(
    event_status: EventStatus, status_server: StatusServer
) -> None:
    """Event list with a missing ID still changes the state of the existing event IDs"""
    events: list[Event] = [
        {
            "host": "ABC1",
            "text": "event1 text",
            "core_host": "ABC",
        },
        {
            "host": "ABC2",
            "text": "event2 text",
            "core_host": "ABC",
        },
    ]
    for event in events:
        event_status.new_event(CMKEventConsole.new_event(event))

    assert len(event_status.events()) == 2

    s = FakeStatusSocket(b"COMMAND DELETE;2;testuser")
    status_server.handle_client(s, True, "127.0.0.1")

    s = FakeStatusSocket(b"COMMAND CHANGESTATE;1,2;testuser;3")

    with pytest.raises(MKClientError):
        status_server.handle_client(s, True, "127.0.0.1")

    assert event_status.events()[0]["state"] == 3

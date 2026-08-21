"""Covers event_log.py, added directly in response to live-troubleshooting
feedback: logs needed a fixed, discoverable location and the wizard needed
a way to show "what just happened" without exposing full debug spam.
"""
import logging
from pathlib import Path

from joystick_notify.event_log import EventLogHandler, read_events


def _logger_with_handler(path):
    logger = logging.getLogger("joystick_notify.test_event_log")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    handler = EventLogHandler(path=path, max_events=5)
    logger.addHandler(handler)
    return logger, handler


def test_info_message_is_captured_and_persisted(tmp_path):
    path = Path(tmp_path) / "events.json"
    logger, _ = _logger_with_handler(path)

    logger.info("state_machine[dev1]: transitioned to couch")

    events = read_events(path)
    assert len(events) == 1
    assert events[0].message == "state_machine[dev1]: transitioned to couch"
    assert events[0].level == "INFO"
    assert events[0].logger == "joystick_notify.test_event_log"


def test_debug_messages_are_not_captured(tmp_path):
    # The whole point: DEBUG-level raw-event spam must not flood the
    # wizard's "what just happened" view.
    path = Path(tmp_path) / "events.json"
    logger, _ = _logger_with_handler(path)

    logger.debug("debounce[dev1]: raw add (class=generic, source=udev)")

    assert read_events(path) == []


def test_ring_buffer_bounded_to_max_events(tmp_path):
    path = Path(tmp_path) / "events.json"
    logger, _ = _logger_with_handler(path)

    for i in range(8):
        logger.info("event %d", i)

    events = read_events(path)
    assert len(events) == 5  # max_events=5
    assert events[0].message == "event 3"  # oldest 3 dropped
    assert events[-1].message == "event 7"


def test_read_events_missing_file_returns_empty_list(tmp_path):
    assert read_events(Path(tmp_path) / "does-not-exist.json") == []


def test_message_formatting_with_args(tmp_path):
    path = Path(tmp_path) / "events.json"
    logger, _ = _logger_with_handler(path)

    logger.info("activity_gate[%s]: real activity observed, forwarding connect", "DEV123")

    events = read_events(path)
    assert events[0].message == "activity_gate[DEV123]: real activity observed, forwarding connect"

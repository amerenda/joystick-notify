"""Covers event_log.py, added directly in response to live-troubleshooting
feedback: logs needed a fixed, discoverable location and the wizard needed
a way to show "what just happened" without exposing full debug spam.

Capture is unfiltered (every level) so a retroactive "let me see the debug
detail from right before this broke" always has data to show; the
reduction to a curated default view happens via filter_events() at read
time instead — see the module docstring for why that split matters.
"""
import logging
from pathlib import Path

from joystick_notify.event_log import HEADLINE, EventLogHandler, filter_events, headline, read_events


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

    logger.info("audio: default -> some-sink")

    events = read_events(path)
    assert len(events) == 1
    assert events[0].message == "audio: default -> some-sink"
    assert events[0].level == "INFO"
    assert events[0].levelno == logging.INFO
    assert events[0].logger == "joystick_notify.test_event_log"


def test_debug_messages_are_captured_but_excluded_from_the_default_filter(tmp_path):
    # Capture no longer excludes DEBUG at the handler level (that was the
    # old design) -- it's captured like everything else, and the wizard's
    # default "Main events" view hides it via filter_events(), not via
    # never having stored it in the first place.
    path = Path(tmp_path) / "events.json"
    logger, _ = _logger_with_handler(path)

    logger.debug("debounce[dev1]: raw add (class=generic, source=udev)")

    events = read_events(path)
    assert len(events) == 1
    assert events[0].level == "DEBUG"
    assert filter_events(events, "main") == []
    assert filter_events(events, "debug") == events


def test_headline_helper_logs_at_headline_level_between_info_and_warning(tmp_path):
    path = Path(tmp_path) / "events.json"
    logger, _ = _logger_with_handler(path)

    headline(logger, "state_machine[dev1]: transitioned to couch")

    events = read_events(path)
    assert events[0].level == "HEADLINE"
    assert events[0].levelno == HEADLINE
    assert logging.INFO < HEADLINE < logging.WARNING


def test_filter_events_main_shows_headline_warning_error_not_info_or_debug(tmp_path):
    path = Path(tmp_path) / "events.json"
    logger, _ = _logger_with_handler(path)

    logger.debug("routine debug detail")
    logger.info("routine info detail")
    headline(logger, "controller dev1 connected")
    logger.warning("something concerning")
    logger.error("something failed")

    events = read_events(path)
    main_view = [e.message for e in filter_events(events, "main")]
    assert main_view == ["controller dev1 connected", "something concerning", "something failed"]


def test_filter_events_info_adds_routine_detail_on_top_of_main(tmp_path):
    path = Path(tmp_path) / "events.json"
    logger, _ = _logger_with_handler(path)

    logger.debug("routine debug detail")
    logger.info("routine info detail")
    headline(logger, "controller dev1 connected")

    events = read_events(path)
    info_view = [e.message for e in filter_events(events, "info")]
    assert info_view == ["routine info detail", "controller dev1 connected"]


def test_filter_events_unrecognized_value_falls_back_to_default(tmp_path):
    path = Path(tmp_path) / "events.json"
    logger, _ = _logger_with_handler(path)

    logger.debug("routine debug detail")
    headline(logger, "controller dev1 connected")

    events = read_events(path)
    assert filter_events(events, "not-a-real-level") == filter_events(events, "main")


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


def test_read_events_tolerates_old_files_with_no_levelno_field(tmp_path):
    # Regression test: events.json written before `levelno` existed on
    # LogEvent must still load (and filter sensibly) rather than KeyError.
    import json

    path = Path(tmp_path) / "events.json"
    path.write_text(json.dumps({
        "events": [
            {"timestamp": 1.0, "level": "WARNING", "logger": "x", "message": "old warning, no levelno"},
        ]
    }))

    events = read_events(path)
    assert events[0].levelno == logging.WARNING
    assert filter_events(events, "main") == events


def test_message_formatting_with_args(tmp_path):
    path = Path(tmp_path) / "events.json"
    logger, _ = _logger_with_handler(path)

    logger.info("activity_gate[%s]: real activity observed, forwarding connect", "DEV123")

    events = read_events(path)
    assert events[0].message == "activity_gate[DEV123]: real activity observed, forwarding connect"

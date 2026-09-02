from joystick_notify.boot_time import is_fresh_boot, read_system_uptime_s


def test_is_fresh_boot_true_below_threshold():
    assert is_fresh_boot(120.0, system_uptime_s=lambda: 5.0) is True


def test_is_fresh_boot_false_at_or_above_threshold():
    assert is_fresh_boot(120.0, system_uptime_s=lambda: 120.0) is False
    assert is_fresh_boot(120.0, system_uptime_s=lambda: 99999.0) is False


def test_read_system_uptime_s_returns_real_value():
    # /proc/uptime is always present on a real Linux test runner -- just
    # confirm it parses to something sane, not a specific value.
    uptime = read_system_uptime_s()
    assert uptime >= 0.0

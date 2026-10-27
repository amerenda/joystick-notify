import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

_path = Path(__file__).parent.parent / "tools" / "8bitdo-probe" / "8bitdo-probe"
_loader = importlib.machinery.SourceFileLoader("bitdo_probe", str(_path))
_spec = importlib.util.spec_from_loader("bitdo_probe", _loader)
probe = importlib.util.module_from_spec(_spec)
sys.modules["bitdo_probe"] = probe
_loader.exec_module(probe)


def make_usb(tmp_path, vid="2dc8", pid="6013", name="8BitDo Ultimate 2", serial="950F5726DC"):
    dev = tmp_path / "3-2"
    dev.mkdir()
    for fname, value in (("idVendor", vid), ("idProduct", pid), ("product", name), ("serial", serial)):
        (dev / fname).write_text(value + "\n")
    return dev


def test_usb_devices_lists_only_8bitdo(tmp_path):
    make_usb(tmp_path)
    other = tmp_path / "1-5"
    other.mkdir()
    (other / "idVendor").write_text("28de\n")
    found = probe.usb_devices(str(tmp_path))
    assert [(d["product_id"], d["serial"]) for d in found] == [("6013", "950F5726DC")]


def test_interfaces_report_driver_hidraw_and_evdev(tmp_path):
    dev = make_usb(tmp_path)
    iface = tmp_path / "3-2:1.0"
    (iface / "0003:2DC8:6013.001A" / "hidraw" / "hidraw1").mkdir(parents=True)
    drivers = tmp_path / "drivers" / "hid-generic"
    drivers.mkdir(parents=True)
    os.symlink(drivers, iface / "driver")
    (iface / "0003:2DC8:6013.001A" / "input" / "input20" / "event7").mkdir(parents=True)
    [i] = probe.interfaces(str(dev))
    assert i["driver"] == "hid-generic" and i["hidraw"] == ["/dev/hidraw1"] and i["events"] == ["/dev/input/event7"]


def test_verdict_xinput_is_a_gamepad_without_paddles():
    msg = probe.verdict("310b", [{"hidraw": [], "events": ["/dev/input/event7"]}])
    assert "XInput" in msg and "Gamepad evdev node present" in msg


def test_verdict_vendor_mode_needs_an_application_on_hidraw():
    msg = probe.verdict("6013", [{"hidraw": ["/dev/hidraw1"], "events": []}])
    assert "only an application that opens hidraw" in msg


def test_verdict_dinput_names_the_steam_path():
    assert "paddles" in probe.verdict("6012", [{"hidraw": ["/dev/hidraw1"], "events": []}])


def test_verdict_unknown_product_id_is_said_plainly():
    assert "unknown product id" in probe.verdict("beef", [])


def test_holders_finds_a_process_with_the_node_open(tmp_path):
    node = tmp_path / "hidraw1"
    node.write_text("")
    proc = tmp_path / "proc"
    fd_dir = proc / "4242" / "fd"
    fd_dir.mkdir(parents=True)
    os.symlink(node, fd_dir / "9")
    (proc / "4242" / "comm").write_text("steam\n")
    assert probe.holders(str(node), str(proc)) == ["steam[4242]"]
    assert probe.holders(str(tmp_path / "other"), str(proc)) == []


def test_listen_counts_reports_and_report_ids(tmp_path):
    import threading
    import time

    fifo = tmp_path / "hidraw"
    os.mkfifo(fifo)

    def writer():
        fd = os.open(fifo, os.O_WRONLY)  # blocks until the listener opens the read end
        for report in (b"\x81\x00\x01", b"\x81\x02", b"\x30"):
            os.write(fd, report)
            time.sleep(0.05)  # one report per read, like a hidraw node
        os.close(fd)

    t = threading.Thread(target=writer)
    t.start()
    count, ids = probe.listen(str(fifo), 0.6)
    t.join()
    assert count == 3 and ids == {0x81, 0x30}

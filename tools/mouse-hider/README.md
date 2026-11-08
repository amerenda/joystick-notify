# mouse-hider

Hides the pointer after N seconds of inactivity and restores it on motion. Standalone: not part of the daemon or the wizard.

    mouse-hider start      # background; always makes the cursor visible first
    mouse-hider stop       # kill the hider AND make the cursor visible
    mouse-hider restore    # make the cursor visible (hider running or not)
    mouse-hider status
    mouse-hider run        # foreground (debugging)

How it hides: it loads KWin's built-in `hidecursor` effect (KWin 6.2+) at runtime while it runs, sets the effect's timeout in kwinrc `[Effect-hidecursor]`, and unloads the effect when it stops. KWin hides the pointer over every window and shows it on the next pointer event. On start the pointer hides right away (the effect's shortest timeout, 1s); the first pointer event switches to `idle_seconds`, because the effect's reconfigure always shows the pointer and motion has just shown it anyway. The effect is never enabled in kwinrc `[Plugins]`, so nothing survives a crash, logout or reboot.

**Always hidden (default).** KWin's stock effect shows the pointer again on every motion, and also whenever KWin itself moves it (a game releasing its pointer lock when you pause), so by default the hider loads `jn_hidecursor` instead: a tiny KWin effect (`tools/kwin-hidecursor`, built and installed by the package) that hides the pointer while loaded, with no timer and no input watching, and also takes an exclusive evdev grab of every mouse-like input device: no motion reaches the compositor and the pointer never comes back while the hider runs. The mouse is unusable during that time. Not grabbed: Sunshine's virtual mouse (`libvirtualhid`, remote streaming input) and anything with gamepad buttons. Grabs are dropped when the process ends (stop, crash, kill), so `ExecStopPost` and a reboot always give the mouse back. If the plugin can't be loaded (for example after a KWin upgrade until the package is rebuilt), the hider logs a warning and falls back to the stock effect on a 1s timeout. To go back to hide-on-idle, set `Environment=MOUSE_HIDER_ALWAYS_HIDDEN=0` in `mouse-hider.service` (systemctl --user edit mouse-hider.service).

Idle mode config: none of its own. The idle timeout is `[mouse_hider] idle_seconds` (whole seconds, minimum 1, default 15) in joystick-notify's `~/.config/joystick-notify/config.toml`, set from the wizard (Launch tab, "Hide Mouse Pointer"). Fixed internally: the pointer stays on the 1s hide-now timeout for the first 30s after start, and `restore` puts back `breeze_cursors` if the transparent `invisible` theme from the old theme-swapping version is still live.

Wizard (Lifecycle Hooks card, Launch tab) - the recommended, crash-proof wiring uses the shipped user unit, whose `ExecStopPost` restores the cursor however the hider ends (stop, crash, kill, logout) and which survives a daemon restart:

    Before couch mode starts:  systemctl --user start mouse-hider.service   (returns once the pointer is hidden)
    Before desk mode starts:   systemctl --user stop mouse-hider.service
    After desk mode starts:    mouse-hider restore

The last line is belt-and-braces and safe to run twice. Hooks never block a switch; a failing one is logged and shows as a `hooks` failure in daemon health. Plain `mouse-hider start` / `mouse-hider stop` also work as hook commands, but then the hider dies with the daemon's cgroup on a daemon restart.

Needs KWin 6.2+ (the `hidecursor` effect), `qdbus6`, `kwriteconfig6`, `plasma-apply-cursortheme`, python-evdev and `input` group membership (to see the first pointer event).

`mouse-hider-restore.service` (user unit, shipped by the package) runs `mouse-hider restore` at every login so a hidden cursor can never survive a reboot.

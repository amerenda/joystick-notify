# mouse-hider

Hides the pointer after N seconds of inactivity and restores it on motion. Standalone: not part of the daemon or the wizard.

    mouse-hider start      # background; always makes the cursor visible first
    mouse-hider stop       # kill the hider AND make the cursor visible
    mouse-hider restore    # make the cursor visible (hider running or not)
    mouse-hider status
    mouse-hider run        # foreground (debugging)

How it hides: it loads KWin's built-in `hidecursor` effect (KWin 6.2+) at runtime while it runs, sets the effect's timeout in kwinrc `[Effect-hidecursor]`, and unloads the effect when it stops. KWin hides the pointer over every window and shows it on the next pointer event. The effect is never enabled in kwinrc `[Plugins]`, so nothing survives a crash, logout or reboot.

Config `~/.config/mouse-hider/config.toml` (all optional): `idle_seconds = 15` (whole seconds, minimum 1), `normal_theme = "breeze_cursors"` (what `restore` puts back if the transparent `invisible` theme from the old theme-swapping version is still live). Flag `--idle` overrides it.

Wizard (Lifecycle Hooks card, Launch tab) - the recommended, crash-proof wiring uses the shipped user unit, whose `ExecStopPost` restores the cursor however the hider ends (stop, crash, kill, logout) and which survives a daemon restart:

    After couch mode starts:   systemctl --user start mouse-hider.service
    Before desk mode starts:   systemctl --user stop mouse-hider.service
    After desk mode starts:    mouse-hider restore

The last line is belt-and-braces and safe to run twice. Hooks never block a switch; a failing one is logged and shows as a `hooks` failure in daemon health. Plain `mouse-hider start` / `mouse-hider stop` also work as hook commands, but then the hider dies with the daemon's cgroup on a daemon restart.

Needs KWin 6.2+ (the `hidecursor` effect), `qdbus6`, `kwriteconfig6` and `plasma-apply-cursortheme`.

`mouse-hider-restore.service` (user unit, shipped by the package) runs `mouse-hider restore` at every login so a hidden cursor can never survive a reboot.

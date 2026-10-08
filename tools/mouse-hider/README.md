# mouse-hider

Hides the pointer after N seconds of inactivity and restores it on motion. Standalone: not part of the daemon or the wizard.

    mouse-hider start      # background; always makes the cursor visible first
    mouse-hider stop       # kill the hider AND make the cursor visible
    mouse-hider restore    # make the cursor visible (hider running or not)
    mouse-hider status
    mouse-hider run        # foreground (debugging)

Config `~/.config/mouse-hider/config.toml` (all optional): `idle_seconds = 15`, `hide_theme = "invisible"`, `normal_theme = "breeze_cursors"`, `exclude_devices = []`. Flags `--idle`, `--hide-theme`, `--normal-theme` override it.

Wizard (Lifecycle Hooks card, Launch tab) - the recommended, crash-proof wiring uses the shipped user unit, whose `ExecStopPost` restores the cursor however the hider ends (stop, crash, kill, logout) and which survives a daemon restart:

    After couch mode starts:   systemctl --user start mouse-hider.service
    Before desk mode starts:   systemctl --user stop mouse-hider.service
    After desk mode starts:    mouse-hider restore

The last line is belt-and-braces and safe to run twice. Hooks never block a switch; a failing one is logged and shows as a `hooks` failure in daemon health. Plain `mouse-hider start` / `mouse-hider stop` also work as hook commands, but then the hider dies with the daemon's cgroup on a daemon restart.

Needs the `invisible` theme (ansible-playbooks `roles/mouse-hide`), `plasma-apply-cursortheme`, `qdbus6`, python-evdev and `input` group membership.

Limitation: clients that draw their own cursor (Steam's in-game overlay) ignore the theme.

`mouse-hider-restore.service` (user unit, shipped by the package) runs `mouse-hider restore` at every login so a hidden cursor can never survive a reboot.

# mouse-hider

Hides the pointer after N seconds of inactivity and restores it on motion. Standalone: not part of the daemon or the wizard.

    mouse-hider start      # background; always makes the cursor visible first
    mouse-hider stop       # kill the hider AND make the cursor visible
    mouse-hider restore    # make the cursor visible (hider running or not)
    mouse-hider status
    mouse-hider run        # foreground (debugging)

Config `~/.config/mouse-hider/config.toml` (all optional): `idle_seconds = 15`, `hide_theme = "invisible"`, `normal_theme = "breeze_cursors"`, `exclude_devices = []`. Flags `--idle`, `--hide-theme`, `--normal-theme` override it.

Wizard: set the couch launch command to `mouse-hider start` (or call it from your launch wrapper) and the teardown command to `mouse-hider stop`.

Needs the `invisible` theme (ansible-playbooks `roles/mouse-hide`), `plasma-apply-cursortheme`, `qdbus6`, python-evdev and `input` group membership.

Limitation: clients that draw their own cursor (Steam's in-game overlay) ignore the theme.

`mouse-hider-restore.service` (user unit, shipped by the package) runs `mouse-hider restore` at every login so a hidden cursor can never survive a reboot.

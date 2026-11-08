/*
 * jn_hidecursor: hides the pointer while the effect is loaded, shows it when
 * unloaded. Unlike KWin's stock "hidecursor" it has no inactivity timer and
 * does not watch input, so nothing (a game releasing its pointer lock, a
 * warp, a stray motion event) can bring the pointer back while it is loaded.
 *
 * Never enabled in kwinrc: mouse-hider loads it per couch session over D-Bus
 * (Effects.loadEffect jn_hidecursor), so a crash, logout or reboot restores
 * the pointer.
 */
#include "effect/effect.h"
#include "effect/effecthandler.h"

namespace KWin
{

class JnHideCursorEffect : public Effect
{
    Q_OBJECT

public:
    JnHideCursorEffect()
    {
        effects->hideCursor();
    }

    ~JnHideCursorEffect() override
    {
        effects->showCursor();
    }

    bool isActive() const override
    {
        return false;
    }
};

KWIN_EFFECT_FACTORY(JnHideCursorEffect, "metadata.json")

} // namespace KWin

#include "jn_hidecursor.moc"

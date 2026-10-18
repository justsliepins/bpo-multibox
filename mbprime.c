/* mbprime DISPLAY: one tiny relative pointer nudge (and back) through XTEST.
 * Wine reads the master pointer's axes when it starts. In a fresh box the last device used is
 * Xwayland's absolute pointer, so Wine finds no relative X/Y axes and ignores raw motion for good
 * ("X/Y axis valuators not found"): DirectInput never sees the mouse move. Run this right before
 * starting the client, so the XTEST pointer (relative axes) is the last device. */
#include <X11/Xlib.h>
#include <X11/extensions/XTest.h>
int main(int argc, char **argv)
{
    Display *d = XOpenDisplay(argc > 1 ? argv[1] : NULL);
    if (!d)
        return 1;
    XTestFakeRelativeMotionEvent(d, 1, 0, CurrentTime);
    XTestFakeRelativeMotionEvent(d, -1, 0, CurrentTime);
    XSync(d, False);
    return 0;
}

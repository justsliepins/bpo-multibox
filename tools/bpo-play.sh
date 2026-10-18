#!/bin/sh
# Black Pearl Online: runs the official launcher (it patches, then starts system64\Game.exe).
# Pass --direct to skip the launcher and start the client straight away.
export WINEPREFIX="$HOME/Games/BlackPearlOnline/prefix"
export WINEDEBUG=-all
cd "$WINEPREFIX/drive_c/BlackPearlOnline" || exit 1
if [ "$1" = "--direct" ]; then
    exec wine 'C:\BlackPearlOnline\system64\Game.exe' pKcfT0PcaX
fi
exec wine 'C:\BlackPearlOnline\Launcher.exe'

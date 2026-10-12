#!/bin/sh
# GDrive: Status（画面下に表示）
. /mnt/us/extensions/gdrive/bin/common.sh
msg "GDrive: last $(get_state status) at $(get_state time)" 34
msg "  +$(get_state added) -$(get_state deleted) ~$(get_state updated)  $(get_state message | cut -c1-50)" 35
msg "  files: $(count_files) in $LOCAL_DIR" 36
msg "  remote: $GDRIVE_REMOTE   power-button sync: $(sh $GD/bin/wake.sh status)" 37
[ -f "$RCLONE" ] || msg "  rclone not installed: run kindlectl.py gdrive-setup" 38

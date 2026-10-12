#!/bin/sh
# GDrive: Sync now（KUAL から。結果は画面下に出る）
. /mnt/us/extensions/gdrive/bin/common.sh
run_detached "$GD/bin/sync.sh" --ui

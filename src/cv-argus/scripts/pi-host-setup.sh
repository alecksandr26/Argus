#!/bin/sh
# One-time Raspberry Pi 5 *host* setup so the cv-argus container can use the CSI camera.
#
#   sudo scripts/pi-host-setup.sh            # grants the user who ran sudo
#   sudo scripts/pi-host-setup.sh someuser   # or an explicit user
#
# Why: with rootless Docker (the default on this Pi), the container's "root" is really the
# unprivileged Docker user, and the host's `video` group membership does NOT carry into the
# container. Raspberry Pi OS makes /dev/dma_heap/* root:video 0660 with no per-user ACL, so
# libcamera's frame-buffer allocator fails ("Could not open any dma-buf provider") and no camera
# is listed. /dev/video* / /dev/media* / /dev/v4l-subdev* only work today because logind adds a
# per-user ACL for whoever is logged into the desktop seat -- which a headless boot in a truck
# can't rely on. This installs a udev rule giving the Docker user an explicit ACL on all four
# node types, at every boot, and applies it to the current nodes right away.
#
# Idempotent. Undo with: sudo rm /etc/udev/rules.d/99-argus-camera-acl.rules && sudo reboot
set -eu

USER_NAME="${1:-${SUDO_USER:-}}"
if [ -z "$USER_NAME" ] || [ "$(id -u)" -ne 0 ]; then
    echo "usage: sudo $0 [user]" >&2
    exit 1
fi
id "$USER_NAME" >/dev/null

RULE=/etc/udev/rules.d/99-argus-camera-acl.rules
cat > "$RULE" <<EOF
# Installed by Argus src/cv-argus/scripts/pi-host-setup.sh -- lets $USER_NAME's rootless Docker
# containers open the Pi camera stack's device nodes (see that script for why).
SUBSYSTEM=="dma_heap", RUN+="/usr/bin/setfacl -m u:$USER_NAME:rw \$env{DEVNAME}"
SUBSYSTEM=="video4linux", RUN+="/usr/bin/setfacl -m u:$USER_NAME:rw \$env{DEVNAME}"
SUBSYSTEM=="media", RUN+="/usr/bin/setfacl -m u:$USER_NAME:rw \$env{DEVNAME}"
EOF
echo "wrote $RULE"

udevadm control --reload
for n in /dev/dma_heap/* /dev/video* /dev/media* /dev/v4l-subdev*; do
    [ -c "$n" ] && setfacl -m "u:$USER_NAME:rw" "$n"
done
echo "granted $USER_NAME rw on the camera/dma-heap nodes (now and on every boot)"

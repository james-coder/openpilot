#!/bin/busybox sh
# PID 1 in a networkless, diskless throwaway QEMU initramfs only.
export PATH=/bin
/bin/busybox --install -s /bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
grep -q 'comma_disposable_usb_lab=1' /proc/cmdline || poweroff -f
touch /disposable-usb-lab
insmod /modules/udc-core.ko || poweroff -f
insmod /modules/dummy_hcd.ko || poweroff -f
insmod /modules/raw_gadget.ko || poweroff -f
echo LAB_KERNEL
uname -r
ls /sys/class/udc
ls /dev/raw-gadget
# The modern stock lab kernel does not contain the C3X physical-host patch.
# Exercise USB-core device default-deny with real descriptor enumeration.
for bus in /sys/bus/usb/devices/usb*; do
  echo 0 > "$bus/authorized_default"
  echo 0 > "$bus/interface_authorized_default"
done
for mode in 0 1 2 3 4 5 6 7 1 0; do
  /gadget "$mode" > /tmp/gadget.log 2>&1 &
  gadget_pid=$!
  sleep 3
  found=0
  for dev in /sys/bus/usb/devices/*-1; do
    [ -f "$dev/descriptors" ] || continue
    found=1
    authorized=$(cat "$dev/authorized")
    bindings=0
    for intf in "$dev"/*:*; do
      [ -L "$intf/driver" ] && bindings=$((bindings+1))
    done
    echo "CASE=$mode authorized=$authorized driver_bindings=$bindings descriptor_bytes=$(wc -c < "$dev/descriptors")"
    [ "$authorized" = 0 ] && [ "$bindings" = 0 ] || echo LAB_FAILURE
    if cmp -s "$dev/descriptors" /expected.bin; then echo PROFILE_MATCH; else echo PROFILE_REJECT; fi
  done
  [ "$found" = 1 ] || echo LAB_NO_DEVICE
  cat /tmp/gadget.log
  kill "$gadget_pid" 2>/dev/null
  wait "$gadget_pid" 2>/dev/null
  sleep 1
done
echo LAB_DONE
poweroff -f

#!/bin/busybox sh
export PATH=/bin:/usr/bin
/bin/busybox --install -s /bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
grep -q 'comma_disposable_usb_lab=1' /proc/cmdline || poweroff -f
touch /disposable-usb-lab
for module in udc-core dummy_hcd raw_gadget mii usbnet cdc-wdm usbserial usb_wwan option qmi_wwan hid hid-generic usbhid usb-storage cdc_ether libcomposite u_ether usb_f_hid usb_f_mass_storage usb_f_ecm; do
  insmod "/modules/$module.ko" || { echo "MODULE_FAILURE=$module"; poweroff -f; }
done
echo BINDING_LAB_KERNEL
uname -r
if grep -q 'comma_lab_controls=1' /proc/cmdline; then
  /usr/bin/python3.10 /usb_lab_bindings.py --positive-controls || echo BINDING_LAB_FAILED
else
  /usr/bin/python3.10 /usb_lab_bindings.py || echo BINDING_LAB_FAILED
fi
poweroff -f

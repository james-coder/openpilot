/* Disposable VM only: descriptor-only Raw Gadget, not modem emulation.
 * No endpoint data, exploit payloads, network or host USB passthrough.
 */
#include <assert.h>
#include <fcntl.h>
#include <linux/usb/raw_gadget.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include "usb_lab_profile.h"

int main(int argc, char **argv) {
  if (argc == 2 && !strcmp(argv[1], "--profile")) {
    return fwrite(expected, 1, sizeof(expected), stdout) == sizeof(expected) ? 0 : 1;
  }
  int allow_config = argc == 3 && !strcmp(argv[2], "--allow-config");
  if ((argc != 2 && !allow_config) || access("/disposable-usb-lab", F_OK)) return 2;
  unsigned mode = strtoul(argv[1], NULL, 10);
  if (mode > 7) return 2;
  unsigned char descriptors[512];
  memcpy(descriptors, expected, sizeof(expected));
  size_t config_len = sizeof(expected)-18;
  if (mode >= 1 && mode <= 6) {
    /* Extra class interface: no functional HID/storage/network implementation. */
    unsigned char extra[] = {9, 4, 5, 0, 0, 0xff, 0, 0, 0};
    if (mode <= 2) { extra[5]=3; extra[6]=1; extra[7]=mode; }
    if (mode == 3) { extra[5]=8; extra[6]=6; extra[7]=0x50; }
    if (mode == 4) { extra[5]=2; extra[6]=6; }
    if (mode == 5) { extra[5]=0xff; extra[6]=0x42; }
    if (mode == 6) { extra[5]=9; } /* Downstream hub personality. */
    memcpy(descriptors+18+config_len,extra,sizeof(extra));
    config_len += sizeof(extra);
    descriptors[20]=config_len; descriptors[21]=config_len>>8; descriptors[22]=6;
  } else if (mode == 7) {
    descriptors[18+9+2] = 7; /* Unexpected interface numbering. */
  }
  int fd = open("/dev/raw-gadget", O_RDWR);
  if (fd < 0) { perror("raw-gadget"); return 1; }
  struct usb_raw_init init = {.driver_name="dummy_udc", .device_name="dummy_udc.0", .speed=USB_SPEED_HIGH};
  if (ioctl(fd, USB_RAW_IOCTL_INIT, &init) || ioctl(fd, USB_RAW_IOCTL_RUN, 0)) { perror("init"); return 1; }
  alarm(20);
  for (int n=0; n<128; n++) {
    struct { struct usb_raw_event event; unsigned char data[256]; } ev = {.event.length=256};
    if (ioctl(fd, USB_RAW_IOCTL_EVENT_FETCH, &ev) < 0) break;
    if (ev.event.type != USB_RAW_EVENT_CONTROL || ev.event.length != sizeof(struct usb_ctrlrequest)) continue;
    struct usb_ctrlrequest ctrl; memcpy(&ctrl,ev.data,sizeof(ctrl));
    struct { struct usb_raw_ep_io io; unsigned char data[512]; } out = {0};
    if (ctrl.bRequest == USB_REQ_GET_DESCRIPTOR && (ctrl.bRequestType & USB_DIR_IN)) {
      unsigned type = ctrl.wValue >> 8;
      if (type==USB_DT_DEVICE) { out.io.length=18; memcpy(out.data,descriptors,18); }
      else if (type==USB_DT_CONFIG) { out.io.length=config_len; memcpy(out.data,descriptors+18,config_len); }
      else { ioctl(fd, USB_RAW_IOCTL_EP0_STALL, 0); continue; }
      if (out.io.length > ctrl.wLength) out.io.length=ctrl.wLength;
      if (ioctl(fd, USB_RAW_IOCTL_EP0_WRITE, &out)<0) break;
    } else if (ctrl.bRequest == USB_REQ_GET_STATUS && (ctrl.bRequestType & USB_DIR_IN)) {
      out.io.length=ctrl.wLength<2?ctrl.wLength:2;
      if (ioctl(fd, USB_RAW_IOCTL_EP0_WRITE, &out)<0) break;
    } else if (ctrl.bRequest==USB_REQ_SET_CONFIGURATION) {
      if (!allow_config) {
        puts("UNEXPECTED_SET_CONFIGURATION"); fflush(stdout);
        ioctl(fd, USB_RAW_IOCTL_EP0_STALL, 0);
        continue;
      }
      for (size_t offset=18; offset+2<=18+config_len; offset+=descriptors[offset]) {
        if (!descriptors[offset]) return 1;
        if (descriptors[offset+1]==USB_DT_ENDPOINT &&
            ioctl(fd, USB_RAW_IOCTL_EP_ENABLE, descriptors+offset)<0) { perror("enable endpoint"); return 1; }
      }
      if (ioctl(fd, USB_RAW_IOCTL_CONFIGURE, 0)<0) { perror("configure"); return 1; }
      if (ioctl(fd, USB_RAW_IOCTL_EP0_READ, &out)<0) break;
      puts("CONFIGURED"); fflush(stdout);
    } else if (allow_config && (ctrl.bRequestType & USB_TYPE_MASK) != USB_TYPE_STANDARD) {
      /* Fixed zero/ACK control replies only; not QMI/AT emulation or USB data. */
      out.io.length=ctrl.wLength<sizeof(out.data)?ctrl.wLength:sizeof(out.data);
      if (ioctl(fd, (ctrl.bRequestType & USB_DIR_IN)?USB_RAW_IOCTL_EP0_WRITE:USB_RAW_IOCTL_EP0_READ, &out)<0) break;
    } else { ioctl(fd, USB_RAW_IOCTL_EP0_STALL, 0); }
  }
  close(fd);
  return 0;
}

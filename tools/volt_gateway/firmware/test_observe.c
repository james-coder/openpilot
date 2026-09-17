#include "observe.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
  vgw_observer o;
  vgw_frame f = {.timestamp_us=10, .address=0x123, .bus=3, .dlc=8, .data={1,2,3,4,5,6,7,8}};
  vgw_frame out;
  vgw_id_stat s;
  uint8_t handle;
  vgw_observer_init(&o);
  assert(vgw_observer_size() == sizeof(o));
  assert(vgw_observer_feed(&o, &f));
  assert(o.received[3] == 1 && !vgw_get_id(&o, 0, &s));
  assert(!vgw_next_observation(&o, 10, 1000, &out, &handle));
  assert(vgw_observe_start(&o, 3, 30, 10));
  assert(vgw_capture_start(&o, 8, 10, 10));
  assert(vgw_observer_feed(&o, &f));
  f.timestamp_us = 20;
  f.data[3] = 42;
  assert(vgw_observer_feed(&o, &f));
  assert(vgw_get_id(&o, 0, &s));
  assert(s.first_us == 10 && s.last.timestamp_us == 20 && s.count == 2);
  assert(s.changed_mask == 8 && s.changes[3] == 1 && s.dlc_mask == 256);
  assert(vgw_get_capture(&o, 1, &out) && out.data[3] == 42);
  f.flags = VGW_EXTENDED;
  assert(vgw_observer_feed(&o, &f));
  assert(vgw_get_id(&o, 1, &s) && s.last.flags == VGW_EXTENDED);
  f.flags = 0;
  f.dlc = 0;
  assert(vgw_observer_feed(&o, &f));
  assert(vgw_get_id(&o, 0, &s) && s.changed_mask == 255 && s.dlc_mask == 257);
  assert(s.last.data[0] == 0);
  f.dlc = 8;
  f.flags = VGW_RTR;
  assert(vgw_observer_feed(&o, &f));
  assert(vgw_get_id(&o, 2, &s) && s.last.data[0] == 0);
  assert(!vgw_get_id(&o, VGW_IDS, &s));
  assert(!vgw_get_capture(&o, VGW_CAPTURE, &out));

  vgw_observer_init(&o);
  assert(vgw_observe_start(&o, 3, 1, 0));
  assert(vgw_capture_start(&o, 8, 1, 0));
  f.flags = 0;
  for (unsigned i = 0; i < 1000; i++) {
    f.address = i;
    f.timestamp_us = i;
    assert(vgw_observer_feed(&o, &f));
  }
  assert(o.capture_count == VGW_CAPTURE && o.capture_drops == 1000 - VGW_CAPTURE);
  assert(o.id_drops == 1000 - VGW_IDS);
  f.timestamp_us = 1000000;
  assert(vgw_observer_feed(&o, &f));
  assert(o.capture_drops == 1000 - VGW_CAPTURE && o.id_drops == 1000 - VGW_IDS);
  assert(vgw_clear_ids(&o, 3) && !vgw_get_id(&o, 0, &s));

  vgw_observer_init(&o);
  f.address = 0x123;
  f.timestamp_us = 0;
  assert(vgw_subscribe(&o, 0, 3, 0x123, 0, 10));
  assert(!vgw_subscribe(&o, 0, 3, 0x124, 0, 10));
  assert(vgw_subscribe(&o, 1, 3, 0x123, 0, 10));
  assert(!vgw_subscribe(&o, 32, 3, 0x123, 0, 10));
  assert(!vgw_subscribe(&o, 2, 4, 0x123, 0, 10));
  assert(!vgw_subscribe(&o, 2, 3, 0x800, 0, 10));
  assert(!vgw_subscribe(&o, 2, 3, 0x123, 0, 0));
  assert(vgw_observer_feed(&o, &f));
  f.timestamp_us = 1;
  assert(vgw_observer_feed(&o, &f));
  assert(o.subscriptions[0].coalesced == 1);
  assert(vgw_next_observation(&o, 1, 1000, &out, &handle) && handle == 0 && out.timestamp_us == 1);
  assert(vgw_next_observation(&o, 1, 1000, &out, &handle) && handle == 1);
  assert(!vgw_next_observation(&o, 1, 1000, &out, &handle));
  f.timestamp_us = 2;
  assert(vgw_observer_feed(&o, &f));
  assert(!vgw_next_observation(&o, 2, 1000, &out, &handle));
  assert(!vgw_next_observation(&o, 100001, 1000, &out, &handle));
  assert(o.subscriptions[0].stale == 1 && o.subscriptions[1].stale == 1);
  f.timestamp_us = 100002;
  assert(vgw_observer_feed(&o, &f));
  assert(vgw_next_observation(&o, 100002, 1000, &out, &handle));
  f.timestamp_us = 100001;  /* backward clock closes capture/subscriptions */
  assert(!vgw_observer_feed(&o, &f));
  assert(!o.subscriptions[0].used && !o.subscriptions[1].used);
  assert(!vgw_subscribe(&o, 0, 3, 0x456, 0, 10)); /* no handle reassignment in this session */

  vgw_observer_init(&o);
  assert(vgw_observe_start(&o, 3, 3600, 0));
  f.timestamp_us = 0;
  f.address = 0x123;
  f.dlc = 8;
  for (unsigned i = 0; i < 100000; i++) {
    f.timestamp_us = i;
    f.data[0] = (uint8_t)i;
    assert(vgw_observer_feed(&o, &f));
  }
  assert(vgw_get_id(&o, 0, &s) && s.changes[0] == UINT16_MAX && s.count == 100000);
  f.bus = 4;
  assert(!vgw_observer_feed(&o, &f));
  f.bus = 3;
  f.dlc = 9;
  assert(!vgw_observer_feed(&o, &f));
  /* Independent bus queues may arrive out of global order, including frames
   * older than the command that opened a capture. Do not cancel that capture. */
  vgw_observer_init(&o);
  assert(vgw_observe_start(&o,3,30,1000));
  assert(vgw_capture_start(&o,8,30,1000));
  f.dlc=8; f.flags=0; f.address=0x123;
  f.bus=0; f.timestamp_us=1100;
  assert(vgw_observer_feed(&o,&f));
  f.bus=3; f.timestamp_us=900;
  assert(vgw_observer_feed(&o,&f));
  assert(o.capture_count==0 && !vgw_get_id(&o,0,&s));
  assert(vgw_subscribe(&o,0,3,0x123,0,10));
  assert(!vgw_next_observation(&o,1200,1000,&out,&handle));
  f.timestamp_us=1050;
  assert(vgw_observer_feed(&o,&f));
  f.timestamp_us=1060;
  assert(vgw_observer_feed(&o,&f));
  assert(o.invalid==0 && o.capture_count==2);
  assert(vgw_get_id(&o,0,&s) && s.count==2 && s.first_us==1050 && s.last.timestamp_us==1060);
  assert(vgw_next_observation(&o,1300,1000,&out,&handle) && out.timestamp_us==1060);
  assert(!vgw_next_observation(&o,1299,1000,&out,&handle)); /* real control-clock regression */
  assert(o.invalid==1 && !o.subscriptions[0].used);
  printf("observer tests passed; host context bytes=%zu\n", sizeof(o));
  return 0;
}

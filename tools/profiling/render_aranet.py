"""Synthetic cabin graph preview; no Bluetooth or vehicle access."""
import math
import sys
import time
import pyray as rl
from openpilot.selfdrive.ui.layouts.settings.aranet import AranetLayout
from openpilot.system.ui.lib.application import gui_app

rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
gui_app.init_window('Aranet preview')
target = rl.load_render_texture(2160, 1080)
layout = AranetLayout()
layout.poll = time.monotonic()
now = time.time()  # noqa: TID251 -- fixture matches persisted wall timestamps
layout.rows = [(now-86400+i*120, 900+450*math.sin(i/65), 23+2*math.sin(i/70), 40+10*math.sin(i/80), 120, 90, -62)
               for i in range(721) if not 350 < i < 390]
layout.message = sys.argv[2] if len(sys.argv) > 2 else 'Synthetic preview | Logging enabled'
rl.begin_texture_mode(target)
layout.render(rl.Rectangle(0, 0, 2160, 1080))
rl.end_texture_mode()
capture = rl.load_image_from_texture(target.texture)
rl.image_flip_vertical(capture)
assert rl.export_image(capture, sys.argv[1])
rl.unload_image(capture)
rl.unload_render_texture(target)

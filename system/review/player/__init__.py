"""Logic behind the on-device Dashcam screen (Settings -> Device -> Dashcam). Pure Python: no UI imports, so it is testable headless.

OPTIONAL. Nothing here may be needed for driving. The widget (selfdrive/ui/layouts/settings/dashcam.py) is the only UI part; it is
offroad-only and fails closed. See docs/how-to/dashcam-on-device.md.
"""

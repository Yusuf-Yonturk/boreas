#!/usr/bin/env python3
"""Live check: asking for a fan's firmware curve leaves the running mode as it was.

Reading the firmware curve makes asus-wmi send the throttle policy again, which switches every
custom curve off and resets the power limits. The service puts the mode back after the first
read and answers later requests from memory. This reads sysfs before and after and compares.
No root needed (the service socket and sysfs reads only). Run it right after a service restart,
so the first request really is a hardware read:  sudo systemctl restart boreasd
"""
import datetime
import json
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boreas.client import DaemonError, call

repo = Path(__file__).resolve().parents[1]
out = repo / 'outputs' / ('fan-curve-live-' + datetime.datetime.now(ZoneInfo('Europe/Istanbul')).strftime('%Y%m%d-%H%M%S'))
out.mkdir(parents=True)
curve_dir = next((p.parent for p in Path('/sys/class/hwmon').glob('hwmon*/name')
                  if p.read_text().strip() == 'asus_custom_fan_curve'), None)
armoury = Path('/sys/class/firmware-attributes/asus-armoury/attributes')
watched = [Path('/sys/firmware/acpi/platform_profile')]
if curve_dir:
    watched += sorted(curve_dir.glob('pwm[0-9]_enable')) + sorted(curve_dir.glob('pwm[0-9]_auto_point*'))
watched += [armoury / name / 'current_value' for name in ('ppt_pl1_spl', 'ppt_pl2_sppt', 'ppt_pl3_fppt', 'nv_dynamic_boost', 'nv_temp_target')]
watched = [p for p in watched if p.exists()]


def read():
    return {str(p): p.read_text().strip() for p in watched}


def differences(a, b):
    return {k: [a[k], b[k]] for k in a if a[k] != b[k]}


report = {}
try:
    try:
        call('default_curve', fan='turbine')
        raise AssertionError('The running service is older than this test; install the new version first')
    except DaemonError as exc:
        assert 'No such fan' in str(exc), f'The running service is older than this test ({exc})'
    state = call('status')
    fans = state['fan_curves']
    assert curve_dir and fans, 'This device has no custom fan curve interface'
    mode = call('get_mode', mode=state['mode'])['mode']
    report.update(mode=state['mode'], fans=fans, fans_apply=mode['fans_apply'], power_apply=mode['power_apply'],
                  cpu_temp=mode.get('cpu_temp'), target_before=(state.get('cpu_temp') or {}).get('target'))
    before = read()
    first = {fan: call('default_curve', fan=fan)['curve'] for fan in fans}   # hardware read + the mode put back
    time.sleep(2)   # one controller tick
    after_first = read()
    again = [{fan: call('default_curve', fan=fan)['curve'] for fan in fans} for _ in range(3)]
    after_again = read()
    limits = {k for k in before if 'ppt_' in k}   # the temperature controller moves these on its own
    report['changed_by_first_read'] = differences({k: v for k, v in before.items() if k not in limits or not mode.get('cpu_temp')},
                                                  {k: v for k, v in after_first.items() if k not in limits or not mode.get('cpu_temp')})
    report['changed_by_repeat'] = differences({k: v for k, v in after_first.items() if k not in limits},
                                              {k: v for k, v in after_again.items() if k not in limits})
    report['same_curve_every_time'] = all(a == first for a in again)
    report['target_after'] = (call('status').get('cpu_temp') or {}).get('target')
    report['notice'] = call('status').get('notice')
    report['passed'] = (not report['changed_by_first_read'] and not report['changed_by_repeat']
                        and report['same_curve_every_time'] and report['target_after'] == report['target_before'])
except Exception as exc:
    report['error'] = str(exc)
(out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
print('REPORT', out / 'report.json')
sys.exit(0 if report.get('passed') else 1)

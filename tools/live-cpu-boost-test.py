#!/usr/bin/env python3
"""Brief CPU boost/EPP check with original mode preference restored in finally."""
import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boreas.client import call

repo = Path(__file__).resolve().parents[1]
out = repo / 'outputs' / ('cpu-boost-live-' + datetime.datetime.now(ZoneInfo('Europe/Istanbul')).strftime('%Y%m%d-%H%M%S'))
out.mkdir(parents=True)
paths = [Path('/sys/devices/system/cpu/cpufreq/boost'), *sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/energy_performance_preference'))]
paths = [p for p in paths if p.exists()]
def read():
    return {str(p): p.read_text().strip() for p in paths}
state = call('status')
assert state['gpu']['backend'] == 'firmware' and state['gpu']['mode'] == 'standard', 'Finish the GPU test first'
mode = state['mode']
original = call('get_mode', mode=mode)['mode'].get('cpu_boost', 'default')
report = {'original_mode': mode, 'original_boost': original, 'before': read()}
try:
    call('update_mode', mode=mode, data={'cpu_boost': 'balanced'})
    report['balanced'] = read()
    assert report['balanced'].get('/sys/devices/system/cpu/cpufreq/boost', '1') == '1'
    assert all(v == 'balance_power' for p, v in report['balanced'].items() if p.endswith('energy_performance_preference'))
    report['passed'] = True
except Exception as exc:
    report['error'] = str(exc)
finally:
    try:
        call('update_mode', mode=mode, data={'cpu_boost': original})
        report['after'] = read()
        report['restored_exactly'] = report['before'] == report['after']
        report['final_notice'] = call('status').get('notice')
    except Exception as exc:
        report['restore_error'] = str(exc)
    (out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('REPORT', out / 'report.json')
sys.exit(0 if report.get('passed') and report.get('restored_exactly') else 1)

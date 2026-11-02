#!/usr/bin/env python3
"""Bounded Eco probe. Requires reviewed installation; always restores firmware Standard."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / 'outputs' / ('gpu-live-' + datetime.datetime.now(ZoneInfo('Europe/Istanbul')).strftime('%Y%m%d-%H%M%S'))
OUT.mkdir(parents=True)
report = {'started': datetime.datetime.now(ZoneInfo('Europe/Istanbul')).isoformat(), 'snapshots': {}, 'steps': [], 'access': {}}
changed = False


def call(*args):
    p = subprocess.run(['/usr/bin/boreasctl', *args], capture_output=True, text=True, timeout=100)
    report['steps'].append({'args': args, 'rc': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
    if p.returncode:
        raise RuntimeError(p.stderr or p.stdout)
    return p.stdout


def snapshot(name):
    d = json.loads(call('status'))
    report['snapshots'][name] = d
    g = d['gpu']
    print(name, json.dumps(g, ensure_ascii=False), flush=True)
    return g


def expect(g, backend, mode):
    assert g['backend'] == backend and g['mode'] == mode and not g.get('backend_error'), g
    assert not g['pending_reboot'] and g['mux'] == 1, g


def access(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        os.close(fd)
        return {'accessible': True}
    except OSError as exc:
        return {'accessible': False, 'errno': exc.errno, 'error': str(exc)}


try:
    for name in ('gpu_engine.py', 'daemon.py', 'gpu.py', 'client.py', 'hw.py'):
        a = REPO / 'boreas' / name
        b = Path('/usr/lib/boreas/boreas') / name
        assert hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest(), f'Install mismatch: {name}'
    expect(snapshot('initial'), 'firmware', 'standard')
    report['powerd_before'] = subprocess.run(['systemctl', 'is-active', 'nvidia-powerd.service'], capture_output=True, text=True).stdout.strip()
    changed = True
    call('gpu-backend', 'ebpf')
    standard = snapshot('ebpf_standard')
    expect(standard, 'ebpf', 'standard')
    device = standard['engine']['device']
    nodes = [f"/dev/dri/{device['card']}", f"/dev/dri/{device['render']}", f"/dev/nvidia{device['minor']}"]
    report['access']['standard_before'] = {p: access(p) for p in nodes}
    call('gpu', 'eco')
    eco = snapshot('eco_immediate')
    expect(eco, 'ebpf', 'eco')
    print('Waiting 60 seconds without GPU queries...', flush=True)
    time.sleep(60)
    eco = snapshot('eco_after_60s')
    expect(eco, 'ebpf', 'eco')
    engine = eco['engine']
    report['real_sleep_confirmed'] = engine.get('runtime_status') == 'suspended' and engine.get('power_state') == 'D3cold'
    report['access']['eco'] = {p: access(p) for p in nodes}
    for p, before in report['access']['standard_before'].items():
        if before['accessible']:
            assert not report['access']['eco'][p]['accessible'], f'Eco failed to block {p}'
    call('gpu', 'standard')
    expect(snapshot('standard_restored'), 'ebpf', 'standard')
    report['access']['standard_after'] = {p: access(p) for p in nodes}
    for p, before in report['access']['standard_before'].items():
        if before['accessible']:
            assert report['access']['standard_after'][p]['accessible'], f'Access not restored: {p}'
    call('gpu-backend', 'firmware')
    expect(snapshot('firmware_restored'), 'firmware', 'standard')
    call('gpu-backend', 'ebpf')
    expect(snapshot('second_enable'), 'ebpf', 'standard')
    call('gpu-backend', 'firmware')
    expect(snapshot('second_disable'), 'firmware', 'standard')
    report['functional_checks_passed'] = True
except BaseException as exc:
    report['error'] = f'{type(exc).__name__}: {exc}'
    print(report['error'], file=sys.stderr, flush=True)
finally:
    if changed:
        try:
            call('gpu-backend', 'firmware')
            expect(snapshot('final'), 'firmware', 'standard')
        except BaseException as exc:
            report['cleanup_error'] = str(exc)
    report['powerd_after'] = subprocess.run(['systemctl', 'is-active', 'nvidia-powerd.service'], capture_output=True, text=True).stdout.strip()
    report['finished'] = datetime.datetime.now(ZoneInfo('Europe/Istanbul')).isoformat()
    (OUT / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print('REPORT', OUT / 'report.json', flush=True)
sys.exit(0 if report.get('functional_checks_passed') and not report.get('cleanup_error') else 1)

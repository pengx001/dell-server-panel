#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用服务器监控面板 —— 飞牛 fnOS 应用
- 自动识别硬件(CPU/GPU/风扇/硬盘/机箱温度), 缺什么藏什么
- 可选: Dell IPMI 风扇控制(含内置温控线程与高温兜底)
- 可选: 访问密码
"""
import json, os, re, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sensors as S

TITLE = os.environ.get('TITLE', 'DELL服务器监控面板')

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.environ.get('TRIM_APPNAME', 'server-panel')
DATA = (os.environ.get('TRIM_APPDEST_VOL', '') + '/@appshare/' + APP) if os.environ.get('TRIM_APPDEST_VOL') else os.environ.get('CONFIG_DIR', HERE)

DEFAULTS = {
    'port': 18410, 'bind': '0.0.0.0', 'refresh': 5,
    'user': '', 'pass': '',
    'fan_control': 'auto',
    'fan_vendor': 'dell',
    'fan_temp_limit': 78,
    'fan_presets': {'quiet': 10, 'standard': 20, 'max': 100},
    'fan_table': [[52, 10], [60, 12], [66, 16], [72, 25], [78, 40]],
}


def env_cfg(c):
    m = {'PORT': ('port', int), 'BIND': ('bind', str), 'REFRESH': ('refresh', int),
         'ENABLE_FAN_CONTROL': ('fan_control', lambda v: v.lower() not in ('false', '0', 'no')),
         'FAN_TEMP_LIMIT': ('fan_temp_limit', int), 'AUTH_USER': ('user', str), 'AUTH_PASS': ('pass', str)}
    for k, (key, fn) in m.items():
        v = os.environ.get(k)
        if v not in (None, ''):
            try:
                c[key] = fn(v)
            except Exception:
                pass
    return c


def load_cfg():
    c = dict(DEFAULTS)
    for p in (os.path.join(DATA, 'config.json'), os.path.join(HERE, 'config.json')):
        try:
            c.update(json.load(open(p)))
            break
        except Exception:
            continue
    return env_cfg(c)


CFG = load_cfg()
PWM_FILE = os.path.join(DATA, '.fan_mode')
DUTY_FILE = os.path.join(DATA, '.fan_duty')
CAPS = {'fan_control': False}


def ipmi_raw(*b):
    try:
        subprocess.run(S.IPMI + ['raw'] + list(b), capture_output=True, timeout=8)
        return True
    except Exception:
        return False


def detect_fan_control():
    if str(CFG.get('fan_control')).lower() in ('false', '0', 'no'):
        return False
    if not S.has('ipmitool'):
        return False
    if not ipmi_raw('0x30', '0x30', '0x01', '0x01'):
        return False
    return True


def read_mode():
    try:
        return open(PWM_FILE).read().strip() or 'auto'
    except Exception:
        return 'auto'


def read_duty():
    for f in (DUTY_FILE, '/run/ipmi-fan-duty'):
        v = (S._read(f) or '').strip()
        if v:
            return v
    return 'auto'


def set_duty(d):
    d = max(10, min(100, int(d)))
    ipmi_raw('0x30', '0x30', '0x01', '0x00')
    ipmi_raw('0x30', '0x30', '0x02', '0xff', '0x%02x' % d)
    try:
        open(DUTY_FILE, 'w').write(str(d))
    except Exception:
        pass
    return d


def set_bmc_auto():
    ipmi_raw('0x30', '0x30', '0x01', '0x01')
    try:
        os.remove(DUTY_FILE)
    except OSError:
        pass


def apply_mode(m):
    try:
        open(PWM_FILE, 'w').write(m)
    except Exception:
        pass
    if m == 'idrac':
        set_bmc_auto()
    elif m.startswith('manual:'):
        set_duty(m.split(':')[1])
    else:
        t = max([v for _, v in S.cpus()[1]], default=0)
        if t >= CFG['fan_temp_limit']:
            return apply_mode('idrac')
        d = CFG['fan_table'][-1][1]
        for lim, val in CFG['fan_table']:
            if t < lim:
                d = val
                break
        set_duty(d)


def thermostat():
    while True:
        try:
            if CAPS['fan_control'] and read_mode() == 'auto':
                t = max([v for _, v in S.cpus()[1]], default=0)
                if t >= CFG['fan_temp_limit']:
                    set_bmc_auto()
                else:
                    apply_mode('auto')
        except Exception:
            pass
        time.sleep(60)


_CACHE = {}


def cached(key, ttl, fn):
    """分级缓存: 避免每次请求都跑 ipmitool/perccli"""
    now = time.time()
    v, ts = _CACHE.get(key, (None, 0))
    if now - ts > ttl:
        try:
            v = fn()
        except Exception:
            pass
        _CACHE[key] = (v, now)
    return v


def refresh(key, fn):
    """强制刷新缓存(供后台采样线程使用)"""
    try:
        _CACHE[key] = (fn(), time.time())
    except Exception:
        pass


def sampler():
    """后台采样慢传感器(ipmitool 风扇约 3.6s): 让页面请求恒定瞬时返回"""
    n = 0
    while True:
        refresh('fans', S.fans)
        refresh('gpu', S.gpu)
        if n % 3 == 0:
            refresh('chassis', S.chassis)
        if n % 12 == 0:
            refresh('disks', S.disks)
        n += 1
        time.sleep(1.5)


def uptime():
    try:
        s = float(open('/proc/uptime').read().split()[0])
        d, h = divmod(int(s), 86400)
        h, m = divmod(h, 3600)
        return '%d天%02d:%02d' % (d, h, m // 60)
    except Exception:
        return '-'


def health(d):
    """整体健康判定: ok / warn / crit"""
    worst = ['ok']

    def bump(v, w, c):
        try:
            v = int(v)
        except Exception:
            return
        if v >= c:
            worst[0] = 'crit'
        elif v >= w and worst[0] == 'ok':
            worst[0] = 'warn'
    for _, v in d.get('cpu_pkgs', []):
        bump(v, 75, 85)
    g = d.get('gpu') or {}
    bump(g.get('temp'), 80, 88)
    for slot, t in d.get('disks', []):
        if t is not None:
            bump(t, 50, 55)
    ch = d.get('chassis') or {}
    bump(ch.get('inlet'), 33, 38)
    return worst[0]


_S = None




def make_title():
    t = str(CFG.get('title') or '').strip()
    if not t or t.lower() in ('auto', '自动'):
        return S.auto_title()
    h = S.hw()
    return t.replace('{model}', h.get('model') or '').replace('{vendor}', h.get('vendor') or '').strip()


def status():
    cores, pkgs = S.cpus()
    fl, red, fsrc = cached('fans', 30, S.fans) or ([], '', '')
    dl, dsrc = cached('disks', 120, S.disks) or ([], '')
    st = S.system()
    osr = S._read('/etc/os-release')
    pretty = ''
    for ln in osr.splitlines():
        if ln.startswith('PRETTY_NAME'):
            pretty = ln.split('=', 1)[1].strip().strip('"')
    d = {'title': make_title(), 'ver': APP_VER, 'host': os.uname().nodename, 'os': pretty,
            'cpu_cores': cores, 'cpu_pkgs': pkgs, 'cpu_max': max([v for _, v in cores], default=0),
            'gpu': cached('gpu', 30, S.gpu), 'fans': fl, 'redundancy': red, 'fan_src': fsrc,
            'disks': dl, 'disk_src': dsrc, 'chassis': cached('chassis', 90, S.chassis), 'system': st,
            'net': S.net(), 'mode': read_mode(), 'duty': read_duty(), 'caps': CAPS, 'uptime': uptime(), 'cpu_pct': S.cpu_pct(), 'hw': S.hw(),
            'cfg': {'refresh': CFG['refresh'], 'presets': CFG['fan_presets']},
            }
    d['health'] = health(d)
    return d


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _auth(self):
        if not CFG.get('user') and not CFG.get('pass'):
            return True
        h = self.headers.get('Authorization', '')
        if h.startswith('Basic '):
            try:
                import base64
                u, p = base64.b64decode(h[6:]).decode().split(':', 1)
                if u == CFG.get('user') and p == CFG.get('pass'):
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="server-panel"')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return False

    def _send(self, body, ct='application/json; charset=utf-8'):
        b = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(200)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self._auth():
            return
        if self.path.startswith('/api/status'):
            try:
                return self._send(json.dumps(status()))
            except Exception as e:
                return self._send(json.dumps({'err': str(e)}))
        try:
            return self._send(open(os.path.join(HERE, 'panel.html'), 'rb').read(), 'text/html; charset=utf-8')
        except Exception:
            return self._send('panel.html missing')

    def do_POST(self):
        if not self._auth():
            return
        try:
            n = int(self.headers.get('Content-Length', 0))
            d = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self._send(json.dumps({'msg': '请求格式错误'}))
        if not CAPS['fan_control']:
            return self._send(json.dumps({'msg': '本机不支持风扇控制（需 Dell 服务器 + ipmitool 免密）'}))
        try:
            if 'duty' in d:
                v = set_duty(d['duty'])
                apply_mode('manual:%d' % v)
                msg = '已设固定 %d%%' % v
            else:
                pre = str(d.get('preset', ''))
                if pre in CFG['fan_presets']:
                    apply_mode('manual:%d' % int(CFG['fan_presets'][pre]))
                    msg = '已切换档位（%d%%）' % int(CFG['fan_presets'][pre])
                elif pre == 'thermo':
                    apply_mode('auto')
                    msg = '已切温控自动（当前 %s%%）' % read_duty()
                elif pre == 'idrac':
                    apply_mode('idrac')
                    msg = '已交还主板/BMC 自动控制'
                else:
                    msg = '未知指令'
        except Exception as e:
            msg = '执行出错: %s' % e
        self._send(json.dumps({'msg': msg}))


def main():
    threading.Thread(target=sampler, daemon=True).start()
    CAPS['fan_control'] = detect_fan_control()
    if CAPS['fan_control']:
        threading.Thread(target=thermostat, daemon=True).start()
    srv = ThreadingHTTPServer((CFG['bind'], int(CFG['port'])), H)
    print('server-panel listening on %s:%s fan_control=%s' % (CFG['bind'], CFG['port'], CAPS['fan_control']), flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()

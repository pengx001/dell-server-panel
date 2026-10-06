#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用硬件传感器采集(自动识别, 缺失自动降级)

支持:
  CPU   : hwmon (coretemp / k10temp / zenpower / cpu_thermal / atk0110 ...)
  GPU   : NVIDIA(nvidia-smi) / AMD(amdgpu hwmon) / Intel(基础)
  风扇  : IPMI(服务器) 优先, 否则 hwmon fan*_input(家用主板)
  硬盘  : perccli(RAID卡) / nvme-cli / smartctl(直连 SATA)
  系统  : 负载 / 内存 / 运行时长 / 网络速率
"""
import os, re, subprocess, time

SUDO = ['sudo', '-n']
_TMP = {}


def sh(args, t=10, sudo=False):
    try:
        a = list(args)
        if sudo:
            if a and a[0] == 'ipmitool':
                a = IPMI + a[1:]          # 本地/远程 IPMI 统一入口
            elif os.geteuid() != 0:
                a = SUDO + a              # 容器内为 root 时无需 sudo
            else:
                a = a
        r = subprocess.run(a, capture_output=True, timeout=t)
        return r.stdout.decode('utf-8', 'replace')
    except Exception:
        return ''


def has(cmd):
    return bool(sh(['which', cmd]).strip())


def _read(p):
    try:
        return open(p).read().strip()
    except Exception:
        return ''


# ---------------- CPU ----------------
CPU_NAMES = ('coretemp', 'k10temp', 'zenpower', 'cpu_thermal', 'atk0110', 'it87', 'nct6775')


def hwmon_list():
    out = []
    base = '/sys/class/hwmon'
    if not os.path.isdir(base):
        return out
    for h in sorted(os.listdir(base)):
        d = os.path.join(base, h)
        n = _read(os.path.join(d, 'name'))
        out.append((d, n))
    return out


def cpus():
    """返回 (核心温度列表, 封装温度列表)"""
    cores, pkgs = [], []
    for d, n in hwmon_list():
        if n not in CPU_NAMES:
            continue
        for f in sorted(os.listdir(d)):
            if not re.match(r'temp\d+_input$', f):
                continue
            try:
                v = int(_read(os.path.join(d, f))) // 1000
            except Exception:
                continue
            lab = _read(os.path.join(d, f.replace('_input', '_label'))) or n
            if lab.startswith('Package') or lab.startswith('Tctl') or lab.startswith('Tdie'):
                pkgs.append([lab, v])
            elif re.match(r'(Core|Tccd|CPU)', lab) or lab == n:
                cores.append([lab, v])
    if not pkgs and cores:
        pkgs = [['CPU', max(v for _, v in cores)]]
    if not cores:
        cores = pkgs
    return cores, pkgs


# ---------------- GPU ----------------
def gpu():
    if has('nvidia-smi'):
        q = 'name,temperature.gpu,fan.speed,utilization.gpu,memory.used,memory.total,power.draw'
        t = sh(['nvidia-smi', '--query-gpu=' + q, '--format=csv,noheader,nounits'], 15)
        if t.strip():
            p = [x.strip() for x in t.strip().splitlines()[0].split(',')]
            if len(p) >= 7:
                return {'vendor': 'NVIDIA', 'name': p[0], 'temp': p[1], 'fan': p[2],
                        'util': p[3], 'mu': p[4], 'mt': p[5], 'power': p[6]}
    # AMD / Intel 集显: 通过 drm 节点
    for card in sorted(os.listdir('/sys/class/drm')) if os.path.isdir('/sys/class/drm') else []:
        if not re.match(r'card\d+$', card):
            continue
        base = '/sys/class/drm/' + card
        if not os.path.isdir(base + '/device'):
            continue
        temp = ''
        for d, n in hwmon_list():
            if card in _read(os.path.join(d, 'name')) or n in ('amdgpu', 'radeon', 'i915'):
                for f in sorted(os.listdir(d)):
                    if f.startswith('temp1_input'):
                        try:
                            temp = str(int(_read(os.path.join(d, f))) // 1000)
                        except Exception:
                            pass
        busy = _read(base + '/device/gpu_busy_percent')
        if temp or busy:
            return {'vendor': 'AMD/Intel', 'name': card, 'temp': temp or '-', 'fan': '-',
                    'util': busy or '-', 'mu': '-', 'mt': '-', 'power': '-'}
    return None


# ---------------- 风扇 ----------------
def fans():
    """返回 (风扇列表 [[名, RPM]], 冗余/备注)"""
    t = sh(['ipmitool', 'sdr', 'type', 'fan'], 10, sudo=True)
    r, red = [], ''
    for ln in t.splitlines():
        p = [x.strip() for x in ln.split('|')]
        if 'Redundancy' in ln:
            red = p[4] if len(p) > 4 else ''
            continue
        m = re.search(r'(\d{3,5}) RPM', ln)
        if p and m:
            r.append([p[0].replace(' RPM', '').strip(), int(m.group(1))])
    if r:
        return r, red, 'ipmi'
    # 家用主板: hwmon fan*_input
    for d, n in hwmon_list():
        for f in sorted(os.listdir(d)):
            if re.match(r'fan\d+_input$', f):
                lab = _read(os.path.join(d, f.replace('_input', '_label'))) or ('%s %s' % (n, f[:4]))
                try:
                    v = int(_read(os.path.join(d, f)))
                except Exception:
                    continue
                if v > 0:
                    r.append([lab, v])
    return r, '', 'hwmon' if r else 'none'


# ---------------- 硬盘 ----------------
def _smart_temp(dev, dtype=None):
    a = ['smartctl', '-A']
    if dtype:
        a += ['-d', dtype]
    a.append(dev)
    t = sh(a, 12, sudo=True)
    if not t:
        return None
    if 'Temperature_Celsius' in t or 'Airflow_Temperature' in t:
        for ln in t.splitlines():
            if 'Temperature_Celsius' in ln or 'Airflow_Temperature' in ln:
                nums = re.findall(r'(\d+)\s*$', ln)
                if nums and 0 < int(nums[-1]) < 90:
                    return int(nums[-1])
    m = re.search(r'Temperature:\s+(\d+)\s+Celsius', t)
    if m:
        return int(m.group(1))
    return None


def disks(perccli='/usr/local/bin/perccli'):
    """返回 (硬盘列表 [[名称, 温度|None]], 来源)"""
    # ① RAID 卡
    if os.path.exists(perccli):
        t = sh([perccli, '/c0/eall/sall', 'show', 'all'], 30, sudo=True)
        if 'Drive Temperature' in t:
            out, slot = [], None
            for ln in t.splitlines():
                m = re.match(r'Drive /c0/e\d+/s(\d+) :$', ln.strip())
                if m:
                    slot = int(m.group(1))
                if slot is None:
                    continue
                m2 = re.search(r'Drive Temperature = *(\d+)C', ln)
                if m2:
                    out.append(['盘%d' % (slot + 1), int(m2.group(1))])
                elif 'Drive Temperature = N/A' in ln:
                    out.append(['盘%d' % (slot + 1), None])
            if out:
                return out, 'perccli'
    out = []
    # ② NVMe
    if has('nvme'):
        for dev in sorted(os.listdir('/dev')) if os.path.isdir('/dev') else []:
            if not re.match(r'nvme\d+n\d+$', dev):
                continue
            t = sh(['nvme', 'smart-log', '/dev/' + dev], 10, sudo=True)
            m = re.search(r'temperature\s*:\s*(\d+)', t, re.I)
            out.append([dev, int(m.group(1)) if m else None])
    # ③ SATA/SAS 直连
    for dev in sorted(os.listdir('/dev')) if os.path.isdir('/dev') else []:
        if not re.match(r'sd[a-z]+$|hd[a-z]+$', dev):
            continue
        out.append([dev, _smart_temp('/dev/' + dev)])
    if out:
        return out, 'smartctl'
    return [], 'none'


# ---------------- 机箱温度(IPMI) ----------------
def chassis():
    t = sh(['ipmitool', 'sdr', 'type', 'temperature'], 10, sudo=True)
    r, tc = {}, 0
    for ln in t.splitlines():
        p = [x.strip() for x in ln.split('|')]
        if len(p) < 5:
            continue
        m = re.search(r'(-?\d+)', p[4])
        if not m:
            continue
        if p[0].startswith('Inlet'):
            r['inlet'] = m.group(1)
        elif p[0].startswith('Exhaust'):
            r['exhaust'] = m.group(1)
        elif p[0] == 'Temp':
            tc += 1
            r['cpu%d' % tc] = m.group(1)
    return r


# ---------------- 系统 ----------------
def system():
    load = _read('/proc/loadavg').split()[:3]
    mem = {}
    for ln in _read('/proc/meminfo').splitlines():
        k, v = ln.split(':', 1)
        mem[k] = int(v.strip().split()[0])
    up = float(_read('/proc/uptime').split()[0] or 0)
    d = int(up // 86400); h = int(up % 86400 // 3600); mi = int(up % 3600 // 60)
    tot = mem.get('MemTotal', 1) / 1024 / 1024
    avail = mem.get('MemAvailable', mem.get('MemFree', 0)) / 1024 / 1024
    return {'load': load, 'mem_total': round(tot, 1), 'mem_used': round(tot - avail, 1),
            'mem_pct': round((tot - avail) / tot * 100) if tot else 0,
            'uptime': '%dd %dh %dm' % (d, h, mi)}


def net_rate():
    def snap():
        r = {}
        for ln in _read('/proc/net/dev').splitlines()[2:]:
            if ':' not in ln:
                continue
            i, v = ln.split(':', 1)
            f = v.split()
            r[i.strip()] = (int(f[0]), int(f[8]))
        return r
    a = snap()
    time.sleep(1)
    b = snap()
    out = []
    for k in b:
        if k == 'lo' or k not in a:
            continue
        rx = (b[k][0] - a[k][0]) / 1024.0
        tx = (b[k][1] - a[k][1]) / 1024.0
        if rx or tx:
            out.append([k, round(rx, 1), round(tx, 1)])
    return out


# ---------------------------------------------------------------- 网络
_net_prev = {}


def net():
    """网卡状态 + 实时速率(/proc/net/dev 差值)"""
    import time as _t
    now = _t.time()
    raw = {}
    try:
        for ln in open('/proc/net/dev'):
            if ':' not in ln:
                continue
            name, rest = ln.split(':', 1)
            f = rest.split()
            raw[name.strip()] = [int(x) for x in f[:16]]
    except Exception:
        return []
    out = []
    for name, c in raw.items():
        if name.startswith(('veth', 'docker', 'br-', 'virbr', 'tun', 'tap', 'lo')):
            continue

        def rd(p):
            try:
                return open(p).read().strip()
            except Exception:
                return ''
        st = rd('/sys/class/net/%s/operstate' % name)
        try:
            sp = int(rd('/sys/class/net/%s/speed' % name) or 0)
        except ValueError:
            sp = 0
        prev = _net_prev.get(name)
        rx = tx = 0.0
        if prev:
            dt = max(0.5, now - prev[2])
            rx = max(0.0, (c[0] - prev[0]) / dt)
            tx = max(0.0, (c[8] - prev[1]) / dt)
        _net_prev[name] = (c[0], c[8], now)
        out.append({'name': name, 'state': st, 'speed': sp if sp > 0 else 0,
                    'rx': round(rx), 'tx': round(tx), 'rx_total': c[0], 'tx_total': c[8],
                    'rx_err': c[2], 'tx_err': c[10], 'rx_drop': c[3], 'tx_drop': c[11]})
    out.sort(key=lambda x: (0 if x['name'].startswith(('eth', 'en', 'eno', 'ens', 'enp')) else 1, x['name']))
    return out

# ---------------------------- IPMI 调用方式(本地 / 远程)
SDR_CACHE = os.environ.get('SDR_CACHE', '/tmp/server_panel.sdr')
_as_root = (os.geteuid() == 0)


def _sdr_ok():
    try:
        return os.path.exists(SDR_CACHE) and os.path.getsize(SDR_CACHE) > 100
    except Exception:
        return False


def sdr_dump():
    base = (['ipmitool'] if _as_root else ['sudo', '-n', 'ipmitool'])
    try:
        subprocess.run(base + ['sdr', 'dump', SDR_CACHE], capture_output=True, timeout=40)
    except Exception:
        pass
    return _sdr_ok()


_host = os.environ.get('IPMI_HOST', '').strip()
if _host:
    IPMI = ['ipmitool', '-I', os.environ.get('IPMI_INTERFACE', 'lanplus'), '-H', _host,
            '-U', os.environ.get('IPMI_USER', 'admin'), '-P', os.environ.get('IPMI_PASS', '')]
else:
    if os.environ.get('IPMI_LOCAL_SUDO', '0' if _as_root else '1') == '0':
        IPMI = ['ipmitool']
    else:
        IPMI = ['sudo', '-n', 'ipmitool']
    sdr_dump()
    if _sdr_ok():
        IPMI = IPMI + ['-S', SDR_CACHE]

PERCCLI_CANDIDATES = [os.environ.get('PERCCLI', ''), '/usr/local/bin/perccli', 'perccli',
                      'storcli', '/opt/MegaRAID/perccli/perccli64']


def perccli_path():
    for c in PERCCLI_CANDIDATES:
        if not c:
            continue
        if c.startswith('/'):
            if os.path.exists(c):
                return c
        else:
            r = subprocess.run(['sh', '-c', 'command -v %s' % c], capture_output=True)
            if r.returncode == 0:
                return c
    return None



_cpu_prev = []


def cpu_pct():
    """CPU 总体使用率(基于 /proc/stat 两次采样差值)"""
    try:
        v = [int(x) for x in open('/proc/stat').readline().split()[1:]]
        idle = v[3] + (v[4] if len(v) > 4 else 0)
        tot = sum(v)
    except Exception:
        return None
    global _cpu_prev
    pct = None
    if _cpu_prev:
        dt = tot - _cpu_prev[1]
        di = idle - _cpu_prev[0]
        if dt > 0:
            pct = round(100.0 * (1 - di / dt), 1)
    _cpu_prev = [idle, tot]
    return pct


def auto_title():
    h = hw()
    m = (h.get('model') or '').strip()
    v = (h.get('vendor') or '').strip()
    for suf in (' Inc.', ' Corporation', ' Computer Inc.', ' Co., Ltd.', ' Ltd.'):
        if v.endswith(suf):
            v = v[:-len(suf)]
    b = (v + ' ' + m).strip() if (m and v and not m.lower().startswith(v.lower())) else (m or v)
    return (b + ' 监控面板') if b else '服务器监控面板'


def hw():
    """自动识别硬件厂商/型号(DMI 优先, IPMI FRU 兜底)"""
    v = m = ''
    try:
        v = open('/sys/class/dmi/id/sys_vendor').read().strip()
    except Exception:
        pass
    try:
        m = open('/sys/class/dmi/id/product_name').read().strip()
    except Exception:
        pass
    if not m:
        try:
            out = subprocess.run(IPMI + ['fru'], capture_output=True, timeout=15).stdout.decode('utf-8', 'replace')
            for ln in out.splitlines():
                if 'Product Name' in ln and not m:
                    m = ln.split(':', 1)[1].strip()
                if 'Product Manufacturer' in ln and not v:
                    v = ln.split(':', 1)[1].strip()
        except Exception:
            pass
    return {'vendor': v, 'model': m}

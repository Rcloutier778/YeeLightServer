import yeelight
import socket
import struct
import time
import subprocess
from yeelightLib import *
logger = getLogger()


USE_MONITOR_ADVERT_BULBS = True
USE_MONITOR_BULB_STATIC = False
USE_MONITOR_BULB_PING = True

SLEEP_TIME = 240


def monitor_advert_bulbs(event, cond):
    """Watch Yeelight SSDP notifications and debounce discovery wakeups.

    Only unsolicited NOTIFY/ssdp:alive or NOTIFY/ssdp:byebye packets are
    treated as advertisements. M-SEARCH requests and HTTP 200 discovery
    responses are logged at DEBUG and ignored. recvfrom() is used so packet
    source IP/port are available in diagnostic logs.
    """
    setprocname('Advert bulbs')

    mcast_group = '239.255.255.250'
    mcast_port = 1982
    advert_debounce_seconds = 10.0
    last_trigger = float('-inf')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', mcast_port))
    mreq = struct.pack('4sl', socket.inet_aton(mcast_group), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    def parse_packet(packet):
        lines = packet.decode('utf-8', errors='replace').splitlines()
        start_line = lines[0].strip() if lines else '<empty>'
        headers = {}
        for line in lines[1:]:
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            headers[key.strip().lower()] = value.strip()
        return start_line, headers

    while True:
        try:
            packet, source = sock.recvfrom(10240)
            start_line, headers = parse_packet(packet)
            source_text = '%s:%s' % (source[0], source[1])
            nts = headers.get('nts', '').lower()
            nt = headers.get('nt', '')
            location = headers.get('location', '')
            model = headers.get('model', '')
            upper_start = start_line.upper()

            is_notify = upper_start.startswith('NOTIFY ')
            is_advert_state = nts in ('ssdp:alive', 'ssdp:byebye')
            identifies_yeelight = (
                location.lower().startswith('yeelight://')
                or bool(model and ('bulb' in nt.lower() or location))
            )
            is_unsolicited_advert = is_notify and is_advert_state and identifies_yeelight

            if not is_unsolicited_advert:
                # Log Yeelight discovery replies at INFO so we can verify their
                # source/type in normal logs while explicitly excluding them from
                # the advertisement trigger. Other SSDP traffic stays DEBUG-level.
                if upper_start.startswith('HTTP/') and (
                    location.lower().startswith('yeelight://') or model
                ):
                    logger.info(
                        'Ignored Yeelight discovery response source=%s type=%r '
                        'nts=%r nt=%r location=%r model=%r',
                        source_text, start_line, nts, nt, location, model,
                    )
                else:
                    logger.debug(
                        'Ignored SSDP packet source=%s type=%r nts=%r nt=%r location=%r model=%r',
                        source_text, start_line, nts, nt, location, model,
                    )
                continue

            now = time.monotonic()
            if now - last_trigger < advert_debounce_seconds:
                logger.debug(
                    'Debounced Yeelight advertisement source=%s type=%r model=%r; '
                    'last discovery wake was %.1fs ago',
                    source_text, start_line, model, now - last_trigger,
                )
                continue

            last_trigger = now
            with cond:
                # If another producer has already raised the shared event,
                # coalesce this notification rather than generating another wake.
                if event.is_set():
                    logger.debug(
                        'Coalesced Yeelight advertisement source=%s type=%r model=%r; '
                        'bulb event is already pending',
                        source_text, start_line, model,
                    )
                    continue
                event.set()
                logger.info(
                    'Advertising bulb wake source=%s type=%r nts=%r nt=%r location=%r model=%r',
                    source_text, start_line, nts, nt, location, model,
                )
                cond.notify_all()
        except Exception:
            logger.exception('Got exception in advert bulb monitor')
            time.sleep(0.5)

def monitor_bulb_static(event, cond):
    """
    Monitors for bulb connection/disconnections. Mainly for disconnections.
    :param event:
    :return:
    """
    setprocname('Static bulbs')
    current_bulbs_ips = sorted(set(bulb['ip'] for bulb in yeelight.discover_bulbs() if bulb['ip'] in BULB_IPS))
    
    while True:
        try:
            found_bulbs_ip = sorted(set(bulb['ip'] for bulb in yeelight.discover_bulbs(1) if bulb['ip'] in BULB_IPS))
            if current_bulbs_ips != found_bulbs_ip:
                # Retry 3 times. Sometimes a bulb doesn't respond for whatever reason.
                for _ in range(3):
                    tmp_found_bulbs_ip = sorted(set(bulb['ip'] for bulb in yeelight.discover_bulbs(0.8) if bulb['ip'] in BULB_IPS))
                    if tmp_found_bulbs_ip == current_bulbs_ips:
                        break
                else:
                    new_ips = set(found_bulbs_ip) - set(current_bulbs_ips)
                    missing_ips = set(current_bulbs_ips) - set(found_bulbs_ip)
                    for new_ip in new_ips:
                        logger.info('Found new bulb at ip addr: %s', new_ip)
                    for missing_ip in missing_ips:
                        logger.info('Missing bulb at ip addr: %s', missing_ip)
                    
                    current_bulbs_ips = found_bulbs_ip
                    with cond:
                        event.set()
                        logger.info("Static bulb")
                        cond.notify()
            time.sleep(SLEEP_TIME)
        except Exception:
            logger.exception("Got exception in static bulb")
            time.sleep(1.0)


def monitor_ping(event, cond):
    setprocname('Ping bulbs')
    import subprocess
    cmd = 'while ping -c 1 -W 2 %s >/dev/null 2>&1; do sleep 2 ; done;'
    proc_dict = {}
    for ip in BULB_IPS:
        proc_dict[ip] = subprocess.Popen((cmd % ip).split(' '))

    while True:
        for ip, proc in proc_dict.items():
            if proc.poll() is not None:
                proc.communicate()
                with cond:
                    event.set()
                    logger.info("ping bulb %s", ip)
                    cond.notify()
                time.sleep(SLEEP_TIME)
                proc_dict[ip] = subprocess.Popen((cmd % ip).split(' '))
        # Polling Popen objects does not block; without this delay this legacy
        # monitor spins at full CPU when all child processes are still running.
        time.sleep(0.1)


def monitor_bulb_ping(event, cond):
    """
    Continuously ping all the bulbs one by one
    """
    setprocname('Ping Bulbs')
    current_bulb_ips = sorted(list(BULB_IPS))
    all_bulb_ips = sorted(list(BULB_IPS))
    check_attempts = 3 # Number of times to double check that a bulb is truely missing
    while True:
        found_bulb_ips = []
        try:
            for ip in all_bulb_ips:
                found_bulb = int(subprocess.run(f'ping -c 1 -W 2 {ip}'.split(' '), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode)==0
                if found_bulb:
                    found_bulb_ips.append(ip)
            if found_bulb_ips != current_bulb_ips:
                new = set(found_bulb_ips) - set(current_bulb_ips)
                missing = set(current_bulb_ips) - set(found_bulb_ips)
                send_cond = False
                if new:
                    logger.info(f'Found new bulbs {new}')
                    send_cond = True
                for missing_ip in list(missing):
                    logger.info(f'Looking for missing bulb {missing_ip}')
                    for _ in range(check_attempts):
                        if int(subprocess.run(f'ping -c 1 -W 2 {missing_ip}'.split(' '), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode)==0:
                            missing -= set([missing_ip])
                            found_bulb_ips.append(missing_ip)
                            logger.info(f'Found missing bulb {missing_ip}')
                            break
                    else:
                        send_cond = True
                found_bulb_ips = sorted(found_bulb_ips)
                if found_bulb_ips == current_bulb_ips:
                    send_cond = False

                current_bulb_ips = list(found_bulb_ips)
                if send_cond:
                    with cond:
                        event.set()
                        logger.info('Ping bulb')
                        cond.notify()
            time.sleep(SLEEP_TIME)
        except Exception:
            logger.exception('Got exception in monitor_bulb_ping')
            time.sleep(1.0)

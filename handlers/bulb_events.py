import yeelight
import socket
import struct
import time

from yeelightLib import getLogger, setprocname
logger = getLogger()

def monitor_advert_bulbs(event, cond):
    """
    Monitors Yeelights default multicast host and port
    Yeelight bulbs will advertise their presence on startup and every 60 minutes afterwards
    :param pipe:
    :return:
    """
    setprocname('Advert bulbs')
    
    # Default yeelight multicast group and port
    MCAST_GRP = '239.255.255.250'
    MCAST_PORT = 1982
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # on this port, receives ALL multicast groups
    sock.bind(('', MCAST_PORT))
    
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    
    while True:
        try:
            res = sock.recv(10240)
            # ssdb:discover is a hallmark of yeelight.discover_bulbs(), which is what we send in static search.
            if b'ssdp:discover' not in res:
                event.set()
                with cond:
                    logger.info("Advertising bulb wake")
                    cond.notify()
        except:
            logger.exception('Got exception in advert bulb!')

def monitor_bulb_static(event, cond):
    """
    Monitors for bulb connection/disconnections. Mainly for disconnections.
    :param event:
    :return:
    """
    setprocname('Static bulbs')
    current_bulbs_ips = sorted(set(bulb['ip'] for bulb in yeelight.discover_bulbs()))
    
    while True:
        try:
            found_bulbs_ip = sorted(set(bulb['ip'] for bulb in yeelight.discover_bulbs(1)))
            if current_bulbs_ips != found_bulbs_ip:
                # Retry 3 times. Sometimes a bulb doesn't respond for whatever reason.
                for _ in range(3):
                    tmp_found_bulbs_ip = sorted(set(bulb['ip'] for bulb in yeelight.discover_bulbs(0.2)))
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
                    event.set()
                    with cond:
                        logger.info("Static bulb")
                        cond.notify()
            time.sleep(60)
        except:
            logger.exception("Got exception in static bulb")

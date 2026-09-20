import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import sys
import types


class FakeBulbException(Exception):
    pass


class FakeTransition:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.kwargs = kwargs

    def __repr__(self):
        return f"{self.__class__.__name__}({self.kwargs!r})"


class FakeFlow:
    class actions:
        stay = "stay"

    def __init__(self, count=1, action=None, transitions=None):
        self.count = count
        self.action = action
        self.transitions = transitions


class FakeBulb:
    instances = []

    def __init__(self, ip, *args, **kwargs):
        self._ip = ip
        self._last_properties = {}
        self.calls = []
        self.properties = {"power": "off", "bright": "20", "ct": "2500"}
        type(self).instances.append(self)

    def get_properties(self, requested_properties=None, ssdp_fallback=False):
        props = requested_properties or self.properties.keys()
        return {key: self.properties[key] for key in props}

    def turn_on(self):
        self.calls.append(("turn_on",))
        self.properties["power"] = "on"

    def turn_off(self):
        self.calls.append(("turn_off",))
        self.properties["power"] = "off"

    def set_brightness(self, value):
        self.calls.append(("set_brightness", value))
        self.properties["bright"] = str(value)

    def set_rgb(self, red, green, blue):
        self.calls.append(("set_rgb", red, green, blue))

    def start_flow(self, flow):
        self.calls.append(("start_flow", flow))

    def listen(self, callback):
        self.calls.append(("listen", callback))


fake_yeelight = types.ModuleType("yeelight")
fake_yeelight.BulbException = FakeBulbException
fake_yeelight.Bulb = FakeBulb
fake_yeelight.Flow = FakeFlow
fake_yeelight.TemperatureTransition = type("TemperatureTransition", (FakeTransition,), {})
fake_yeelight.RGBTransition = type("RGBTransition", (FakeTransition,), {})
fake_yeelight.HSVTransition = type("HSVTransition", (FakeTransition,), {})
fake_yeelight.discover_bulbs = lambda *args, **kwargs: []

main_mod = types.ModuleType("yeelight.main")
main_mod.DEFAULT_PROPS = ("power", "bright", "ct")
main_mod.BulbException = FakeBulbException
transitions_mod = types.ModuleType("yeelight.transitions")
enums_mod = types.ModuleType("yeelight.enums")
aio_mod = types.ModuleType("yeelight.aio")
fake_yeelight.main = main_mod
fake_yeelight.transitions = transitions_mod
fake_yeelight.enums = enums_mod
fake_yeelight.aio = aio_mod

sys.modules["yeelight"] = fake_yeelight
sys.modules["yeelight.main"] = main_mod
sys.modules["yeelight.transitions"] = transitions_mod
sys.modules["yeelight.enums"] = enums_mod
sys.modules["yeelight.aio"] = aio_mod


class FakeInfluxWriter:
    def __init__(self):
        self.calls = []

    def write(self, *args):
        self.calls.append(args)


class FakeInfluxClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.writer = FakeInfluxWriter()
        self.closed = False

    def write_api(self, write_options=None):
        return self.writer

    def close(self):
        self.closed = True


influx_mod = types.ModuleType("influxdb_client")
influx_mod.InfluxDBClient = FakeInfluxClient
influx_client_mod = types.ModuleType("influxdb_client.client")
write_api_mod = types.ModuleType("influxdb_client.client.write_api")
write_api_mod.SYNCHRONOUS = object()
influx_client_mod.write_api = write_api_mod
influx_mod.client = influx_client_mod
sys.modules["influxdb_client"] = influx_mod
sys.modules["influxdb_client.client"] = influx_client_mod
sys.modules["influxdb_client.client.write_api"] = write_api_mod

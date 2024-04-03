from ploomber_core.telemetry import telemetry
from ploomber_core.telemetry.telemetry import Telemetry

try:
    from importlib.metadata import version
except ModuleNotFoundError:
    from importlib_metadata import version


def check_telemetry_enabled():
    return False


telemetry.check_telemetry_enabled = check_telemetry_enabled
telemetry = Telemetry(
    api_key="phc_P9SpSeypyPwxrMdFn2edOOEooQioF2axppyEeDwtMSP",
    package_name="jupysql",
    version=version("jupysql"),
    print_cloud_message=False,
)

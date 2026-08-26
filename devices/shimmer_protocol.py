"""Low-level Shimmer3 Bluetooth protocol helpers used by the HINT console.

This module intentionally has no Qt or pyserial dependency, which keeps the
binary framing/parser unit-testable without Shimmer hardware attached.

The HINT study uses the Shimmer3 GSR+ board with:
    * GSR
    * optical pulse / PPG on internal ADC A13

The sensor selection byte mask below follows the Shimmer3 24-bit sensor mask:
GSR = 0x000004 and internal ADC A13 = 0x000100.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Iterable

# LiteProtocol command / response bytes used by the console.
DATA_PACKET = 0x00
INQUIRY_COMMAND = 0x01
INQUIRY_RESPONSE = 0x02
GET_SAMPLING_RATE_COMMAND = 0x03
SAMPLING_RATE_RESPONSE = 0x04
SET_SAMPLING_RATE_COMMAND = 0x05
START_STREAMING_COMMAND = 0x07
SET_SENSORS_COMMAND = 0x08
STOP_STREAMING_COMMAND = 0x20
SET_GSR_RANGE_COMMAND = 0x21
GET_FW_VERSION_COMMAND = 0x2E
FW_VERSION_RESPONSE = 0x2F
GET_DEVICE_VERSION_COMMAND = 0x3F
DEVICE_VERSION_RESPONSE = 0x25
SET_INTERNAL_EXP_POWER_ENABLE_COMMAND = 0x5E
GET_DAUGHTER_CARD_ID_COMMAND = 0x66
DAUGHTER_CARD_ID_RESPONSE = 0x65
GET_STATUS_COMMAND = 0x72
STATUS_RESPONSE = 0x71
TEST_CONNECTION_COMMAND = 0x96
ACK_COMMAND_PROCESSED = 0xFF

# Sensor bitmap for the study's GSR + PPG configuration.
SENSOR_GSR = 0x000004
SENSOR_INT_A13_PPG = 0x000100
HINT_SENSOR_MASK = SENSOR_GSR | SENSOR_INT_A13_PPG

# Inquiry channel identifiers for classic Shimmer3 LogAndStream/BtStream.
CHANNEL_PPG_A13 = 0x12
CHANNEL_GSR = 0x1C

# Shimmer RTC / sampling base clock.
SHIMMER_CLOCK_HZ = 32768.0

# GSR auto-range value used by Shimmer APIs.
GSR_RANGE_AUTO = 4


@dataclass(frozen=True)
class ChannelSpec:
    channel_id: int
    name: str
    fmt: str
    size: int


CHANNEL_FORMATS: dict[int, ChannelSpec] = {
    CHANNEL_PPG_A13: ChannelSpec(CHANNEL_PPG_A13, "ppg_raw", "<H", 2),
    CHANNEL_GSR: ChannelSpec(CHANNEL_GSR, "gsr_raw", "<H", 2),
}


@dataclass(frozen=True)
class InquiryInfo:
    sampling_divisor: int
    sampling_rate_hz: float
    num_channels: int
    buffer_size: int
    channel_ids: tuple[int, ...]
    config_bytes: bytes


@dataclass(frozen=True)
class ShimmerFrame:
    timestamp_raw: int
    values: dict[str, int]
    gsr_adc: int | None
    gsr_range: int | None


def sampling_divisor(rate_hz: float) -> int:
    """Return the 16-bit divisor sent to SET_SAMPLING_RATE."""
    if rate_hz <= 0:
        raise ValueError("Sampling rate must be > 0")
    divisor = int(SHIMMER_CLOCK_HZ / float(rate_hz))
    if not 1 <= divisor <= 0xFFFF:
        raise ValueError(f"Sampling rate {rate_hz} Hz produces invalid divisor {divisor}")
    return divisor


def actual_sampling_rate(divisor: int) -> float:
    if divisor <= 0:
        raise ValueError("Sampling divisor must be > 0")
    return SHIMMER_CLOCK_HZ / divisor


def set_sampling_rate_command(rate_hz: float) -> bytes:
    divisor = sampling_divisor(rate_hz)
    return bytes((
        SET_SAMPLING_RATE_COMMAND,
        divisor & 0xFF,
        (divisor >> 8) & 0xFF,
    ))


def sensor_mask_bytes(mask: int = HINT_SENSOR_MASK) -> bytes:
    if not 0 <= mask <= 0xFFFFFF:
        raise ValueError("Shimmer3 sensor mask must fit in 24 bits")
    return bytes((mask & 0xFF, (mask >> 8) & 0xFF, (mask >> 16) & 0xFF))


def set_sensors_command(mask: int = HINT_SENSOR_MASK) -> bytes:
    return bytes((SET_SENSORS_COMMAND,)) + sensor_mask_bytes(mask)


def parse_firmware_payload(payload: bytes) -> tuple[int, int, int, int]:
    """Parse the six FW-version payload bytes after response opcode 0x2F."""
    if len(payload) < 6:
        raise ValueError(f"Firmware payload too short: {len(payload)}")
    fw_id = payload[0] | (payload[1] << 8)
    major = payload[2] | (payload[3] << 8)
    minor = payload[4]
    internal = payload[5]
    return fw_id, major, minor, internal


def timestamp_bytes_for_firmware(
    fw_id: int | None,
    major: int | None,
    minor: int | None,
) -> int:
    """Choose timestamp width for LogAndStream-compatible classic Shimmer3.

    LogAndStream 0.6 introduced the 3-byte timestamp used by current
    firmware. For unknown/current firmware we prefer 3 bytes; old 0.x
    versions below 0.6 fall back to 2 bytes.
    """
    if major is None or minor is None:
        return 3
    if major > 0:
        return 3
    return 3 if minor >= 6 else 2


def parse_inquiry_response(message: bytes) -> InquiryInfo:
    """Parse a complete inquiry response, including leading 0x02 opcode.

    Classic Shimmer3 inquiry body layout used here:
        [sampling divisor:2][config bytes:4][num channels:1][buffer size:1]
        [channel ids:num_channels]
    """
    if not message or message[0] != INQUIRY_RESPONSE:
        raise ValueError("Not an inquiry response")
    if len(message) < 9:
        raise ValueError(f"Inquiry response too short: {len(message)}")

    body = message[1:]
    divisor = body[0] | (body[1] << 8)
    config_bytes = bytes(body[2:6])
    num_channels = body[6]
    buffer_size = body[7]
    expected = 1 + 8 + num_channels
    if len(message) < expected:
        raise ValueError(
            f"Incomplete inquiry response: expected {expected} bytes, got {len(message)}"
        )
    channel_ids = tuple(message[9:expected])
    return InquiryInfo(
        sampling_divisor=divisor,
        sampling_rate_hz=actual_sampling_rate(divisor),
        num_channels=num_channels,
        buffer_size=buffer_size,
        channel_ids=channel_ids,
        config_bytes=config_bytes,
    )


def inquiry_message_length(prefix: bytes | bytearray) -> int | None:
    """Return expected full inquiry message length once numChannels is known."""
    if len(prefix) < 8 or prefix[0] != INQUIRY_RESPONSE:
        return None
    # Message index 7 = body offset 6 = numChannels.
    num_channels = prefix[7]
    return 1 + 8 + num_channels


def frame_length(channel_ids: Iterable[int], timestamp_bytes: int = 3) -> int:
    total = 1 + timestamp_bytes
    for channel_id in channel_ids:
        spec = CHANNEL_FORMATS.get(channel_id)
        if spec is None:
            raise ValueError(
                f"Unsupported enabled Shimmer channel 0x{channel_id:02X}; "
                "HINT expects only PPG/A13 and GSR"
            )
        total += spec.size
    return total


def parse_data_frame(
    frame: bytes,
    channel_ids: Iterable[int],
    timestamp_bytes: int = 3,
) -> ShimmerFrame:
    channel_ids = tuple(channel_ids)
    expected = frame_length(channel_ids, timestamp_bytes)
    if len(frame) != expected:
        raise ValueError(f"Expected {expected}-byte frame, got {len(frame)}")
    if frame[0] != DATA_PACKET:
        raise ValueError("Frame does not start with DATA_PACKET (0x00)")
    if timestamp_bytes not in (2, 3):
        raise ValueError("timestamp_bytes must be 2 or 3")

    if timestamp_bytes == 3:
        timestamp_raw = frame[1] | (frame[2] << 8) | (frame[3] << 16)
    else:
        timestamp_raw = frame[1] | (frame[2] << 8)

    offset = 1 + timestamp_bytes
    values: dict[str, int] = {}
    for channel_id in channel_ids:
        spec = CHANNEL_FORMATS[channel_id]
        values[spec.name] = struct.unpack_from(spec.fmt, frame, offset)[0]
        offset += spec.size

    gsr_raw = values.get("gsr_raw")
    gsr_adc = None if gsr_raw is None else (gsr_raw & 0x3FFF)
    gsr_range = None if gsr_raw is None else ((gsr_raw >> 14) & 0x03)
    return ShimmerFrame(
        timestamp_raw=timestamp_raw,
        values=values,
        gsr_adc=gsr_adc,
        gsr_range=gsr_range,
    )

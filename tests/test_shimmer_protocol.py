"""Unit tests for the Shimmer binary protocol layer (no hardware/Qt needed)."""

from devices import shimmer_protocol as p


def test_hint_sensor_mask_is_gsr_plus_ppg_a13_lsb_first():
    assert p.HINT_SENSOR_MASK == 0x000104
    assert p.sensor_mask_bytes() == bytes([0x04, 0x01, 0x00])
    assert p.set_sensors_command() == bytes([p.SET_SENSORS_COMMAND, 0x04, 0x01, 0x00])


def test_128_hz_sampling_command_uses_32768_clock_divisor():
    assert p.sampling_divisor(128.0) == 256
    assert p.set_sampling_rate_command(128.0) == bytes([p.SET_SAMPLING_RATE_COMMAND, 0x00, 0x01])
    assert p.actual_sampling_rate(256) == 128.0


def test_parse_inquiry_response_for_ppg_and_gsr():
    # opcode, divisor=256, four config bytes, nChannels=2, buffer=1,
    # channel IDs: PPG/A13 then GSR.
    message = bytes([
        p.INQUIRY_RESPONSE,
        0x00, 0x01,
        0x00, 0x00, 0x00, 0x00,
        0x02,
        0x01,
        p.CHANNEL_PPG_A13,
        p.CHANNEL_GSR,
    ])
    info = p.parse_inquiry_response(message)
    assert info.sampling_divisor == 256
    assert info.sampling_rate_hz == 128.0
    assert info.num_channels == 2
    assert info.channel_ids == (p.CHANNEL_PPG_A13, p.CHANNEL_GSR)
    assert p.frame_length(info.channel_ids, timestamp_bytes=3) == 8


def test_parse_stream_frame_extracts_timestamp_ppg_and_gsr_range_bits():
    # timestamp = 0x030201; PPG = 0x1234; GSR raw = range 2 + ADC 0x0123.
    gsr_raw = (2 << 14) | 0x0123
    frame = bytes([
        p.DATA_PACKET,
        0x01, 0x02, 0x03,
        0x34, 0x12,
        gsr_raw & 0xFF, (gsr_raw >> 8) & 0xFF,
    ])
    sample = p.parse_data_frame(
        frame,
        (p.CHANNEL_PPG_A13, p.CHANNEL_GSR),
        timestamp_bytes=3,
    )
    assert sample.timestamp_raw == 0x030201
    assert sample.values["ppg_raw"] == 0x1234
    assert sample.values["gsr_raw"] == gsr_raw
    assert sample.gsr_adc == 0x0123
    assert sample.gsr_range == 2


def test_current_logandstream_firmware_uses_three_byte_timestamp():
    assert p.timestamp_bytes_for_firmware(3, 0, 6) == 3
    assert p.timestamp_bytes_for_firmware(3, 1, 0) == 3
    assert p.timestamp_bytes_for_firmware(3, 0, 5) == 2

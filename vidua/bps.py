"""Validate and apply BPS patches."""
import logging
import zlib
from io import BytesIO
from typing import BinaryIO

logger = logging.getLogger(__name__)

def decode_number(bps_patch: BinaryIO) -> int:
    """Return the next number in ``bps_patch``.

    :param bps_patch: the patch file
    """
    data = 0
    shift = 1
    while True:
        pos = bps_patch.tell()
        end = bps_patch.seek(0, 2)
        if pos > end - 12:
            raise ValueError("Invalid number encoding.")
        bps_patch.seek(pos)
        x = bps_patch.read(1)
        x = ord(x)
        data += (x & 0x7f) * shift
        if (x & 0x80):
            break
        else:
            shift <<= 7
            data += shift
    return data

def patch_info(bps_patch: BinaryIO) -> dict:
    """Return a dictionary of information about the patch.

        >>> patch_info(bps_patch)
        {'target_size': 24,
         'metadata': b'',
         'final_checksum': 2648610592,
         'source_checksum': 3418748557,
         'source_size': 37}

    :param bps_patch: the patch file
    """
    validate_patch(bps_patch)
    info = {}
    bps_patch.seek(4)
    info['source_size'] = decode_number(bps_patch)
    info['target_size'] = decode_number(bps_patch)
    metadata_size = decode_number(bps_patch)
    info['metadata'] = bps_patch.read(metadata_size)
    bps_patch.seek(-12, 2)
    info['source_checksum'] = int.from_bytes(bps_patch.read(4), byteorder='little')
    info['final_checksum'] = int.from_bytes(bps_patch.read(4), byteorder='little')
    bps_patch.seek(0)
    return info

def validate_patch(bps_patch: BinaryIO):
    """Verify that ``bps_patch`` is a valid BPS patch.

    If the patch is valid, return. If the patch is invalid, raise a
    ``ValueError`` describing the problem.

    :param bps_patch: the patch file
    """
    bps_patch.seek(0)
    if bps_patch.read(4) != b'BPS1':
        raise ValueError("Invalid file format marker.")
    
    patch_end = bps_patch.seek(0, 2)
    if patch_end < 19:
        raise ValueError("Patch too short.")
    
    bps_patch.seek(0)
    calculated_checksum = zlib.crc32(bps_patch.read(patch_end - 4))
    checksum = int.from_bytes(bps_patch.read(4), byteorder='little')
    if calculated_checksum != checksum:
        raise ValueError("Invalid checksum. Stored checksum {:X}, actual checksum {:X}.".format(checksum, calculated_checksum))
    
    bps_patch.seek(4)

    try:
        source_size = decode_number(bps_patch)
        logger.debug("Source size: 0x%x", source_size)
    except ValueError:
        raise ValueError("Failed to decode source size.")

    try:
        target_size = decode_number(bps_patch)
        logger.debug("Target size: 0x%x", target_size)
    except ValueError:
        raise ValueError("Failed to decode target size.")
    
    try:
        metadata_size = decode_number(bps_patch)
        logger.debug("Metadata size: 0x%x", metadata_size)
    except ValueError:
        raise ValueError("Failed to decode metadata size.")

    if metadata_size + bps_patch.tell() > patch_end - 12:
        raise ValueError("Metadata size too large.")

    bps_patch.seek(metadata_size, 1)

    source_position = 0
    target_position = 0
    outread_position = 0

    while bps_patch.tell() < patch_end - 12:
        bps_pos = bps_patch.tell()
        data = decode_number(bps_patch)
        command = data & 3;
        length = (data >> 2) + 1
        error_details = "Offset: 0x{:x}\nCommand: {:d}\nLength: 0x{:x}\nSource position: 0x{:x}\nTarget position: 0x{:x}".format(
            bps_pos, command, length, source_position, target_position)
        # logger.debug("Decoded a command.\n%s", error_details)
        if command == 0:
            # SourceRead
            target_position += length
            if target_position > source_size:
                raise ValueError("Attempted to read beyond end of source.\n{}".format(error_details))
            if target_position > target_size:
                raise ValueError("Attempted to write beyond end of target.\n{}".format(error_details))
        elif command == 1:
            # TargetRead
            target_position += length
            if target_position > target_size:
                raise ValueError("Attempted to write beyond end of target.\n{}".format(error_details))
            bps_patch.seek(length, 1)
            if bps_patch.tell() > patch_end - 12:
                raise ValueError("TargetRead length too large.\n{}".format(error_details))
        elif command == 2:
            # SourceCopy
            copy_data = decode_number(bps_patch)
            source_relative_offset = (-1 if (copy_data & 1) else 1) * (copy_data >> 1)
            error_details += "\nSRO: {}".format(source_relative_offset)
            if source_position + source_relative_offset < 0:
                raise ValueError("Attempted to read beyond beginning of source.\n{}".format(error_details))
            source_position += source_relative_offset + length
            if source_position > source_size:
                raise ValueError("Attempted to read beyond end of source.\n{}".format(error_details))
            target_position += length
            if target_position > target_size:
                raise ValueError("Attempted to write beyond end of target.\n{}".format(error_details))
        elif command == 3:
            # TargetCopy
            copy_data = decode_number(bps_patch)
            target_relative_offset = (-1 if (copy_data & 1) else 1) * (copy_data >> 1)
            outread_position += target_relative_offset
            error_details += "\nTRO: {}".format(target_relative_offset)
            if outread_position < 0:
                raise ValueError("Attempted to read beyond beginning of target.\n{}".format(error_details))
            if outread_position >= target_position:
                raise ValueError("Attempted to read beyond end of target.\n{}".format(error_details))
            target_position += length
            if target_position > target_size:
                raise ValueError("Attempted to write beyond end of target.\n{}".format(error_details))
            outread_position += length
    
    if target_position != target_size:
        raise ValueError("Final patch size incorrect. Expected: {}. Actual: {}".format(target_size, target_position))

def patch(source: BinaryIO, bps_patch: BinaryIO) -> BinaryIO:
    """Return the patched source.

    :param source: the source file to be patched
    :param bps_patch: the patch file
    """
    bps_patch.seek(0)
    validate_patch(bps_patch)

    bps_patch.seek(-12, 2)
    checksum = int.from_bytes(bps_patch.read(4), byteorder='little')
    source.seek(0)
    calculated_checksum = zlib.crc32(source.read())
    if calculated_checksum != checksum:
        raise ValueError("Incompatible source. Stored checksum {:X}, actual checksum {:X}.".format(checksum, calculated_checksum))
    source.seek(0)

    patch_end = bps_patch.seek(0, 2)
    bps_patch.seek(4)
    source_size = decode_number(bps_patch)
    target_size = decode_number(bps_patch)
    metadata_size = decode_number(bps_patch)
    bps_patch.seek(metadata_size, 1)

    source_position = 0
    outread_position = 0
    output = BytesIO()

    while bps_patch.tell() < patch_end - 12:
        bps_pos = bps_patch.tell()
        data = decode_number(bps_patch)
        command = data & 3;
        length = (data >> 2) + 1
        if command == 0:
            # SourceRead
            old_source_position = source.tell()
            source.seek(output.tell())
            to_go = length
            while to_go > 0:
                output.write(source.read(min(to_go, 2**8)))
                to_go -= 2**8
            source.seek(old_source_position)
        elif command == 1:
            # TargetRead
            to_go = length
            while to_go > 0:
                output.write(bps_patch.read(min(to_go, 2**8)))
                to_go -= 2**8
        elif command == 2:
            # SourceCopy
            copy_data = decode_number(bps_patch)
            source_relative_offset = (-1 if (copy_data & 1) else 1) * (copy_data >> 1)
            source.seek(source_relative_offset, 1)
            to_go = length
            while to_go > 0:
                output.write(source.read(min(to_go, 2**8)))
                to_go -= 2**8
        elif command == 3:
            # TargetCopy
            copy_data = decode_number(bps_patch)
            target_relative_offset = (-1 if (copy_data & 1) else 1) * (copy_data >> 1)
            outread_position += target_relative_offset
            to_go = length
            while to_go > 0:
                step = min(output.tell() - outread_position, 2**8)
                output.seek(outread_position)
                segment = output.read(min(to_go, step))
                outread_position = output.tell()
                output.seek(0, 2)
                output.write(segment)
                to_go -= step

    bps_patch.seek(-8, 2)
    checksum = int.from_bytes(bps_patch.read(4), byteorder='little')
    output.seek(0)
    calculated_checksum = zlib.crc32(output.read())
    if calculated_checksum == checksum:
        logger.debug("Patch applied successfully.")
    else:
        raise ValueError("Invalid checksum. Stored checksum {:X}, actual checksum {:X}.".format(checksum, calculated_checksum))
    
    output.seek(0)
    return output

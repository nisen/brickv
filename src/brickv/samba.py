# -*- coding: utf-8 -*-
"""
brickv (Brick Viewer)
Copyright (C) 2012 Matthias Bolte <matthias@tinkerforge.com>

samba.py: Atmel SAM-BA flash protocol implementation

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public
License along with this program; if not, write to the
Free Software Foundation, Inc., 59 Temple Place - Suite 330,
Boston, MA 02111-1307, USA.
"""

import os
import sys
import errno
import glob
import struct
from PyQt4.QtGui import QApplication
from serial import Serial, SerialException

if sys.platform == 'linux2':
    def get_serial_ports():
        ports = []
        for tty in glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*'):
            ports.append((tty, tty, tty))
        return ports
elif sys.platform == 'darwin':
    def get_serial_ports():
        ports = []
        for tty in glob.glob('/dev/tty.*'):
            ports.append((tty, tty, tty))
        return ports
elif sys.platform == 'win32':
    import win32com.client
    def get_serial_ports():
        wmi = win32com.client.GetObject('winmgmts:')
        ports = []
        for port in wmi.InstancesOf('Win32_SerialPort'):
            ports.append((port.DeviceID, port.Name, ''))
        return ports
else:
    def get_serial_ports():
        return []

CHIPID_CIDR = 0x400e0740

ATSAM3SxB = 0x89
ATSAM3SxC = 0x8A

EEFC_FMR = 0x400E0A00
EEFC_FCR = 0x400E0A04
EEFC_FSR = 0x400E0A08
EEFC_FRR = 0x400E0A0C

EEFC_FSR_FRDY   = 0b0001
EEFC_FSR_FCMDE  = 0b0010
EEFC_FSR_FLOCKE = 0b0100

EEFC_FCR_FKEY = 0x5A

EEFC_FCR_FCMD_WP   = 0x01 # Write Page
EEFC_FCR_FCMD_EA   = 0x05 # Erase All
EEFC_FCR_FCMD_SLB  = 0x08 # Set Lock Bit
EEFC_FCR_FCMD_CLB  = 0x09 # Clear Lock Bit
EEFC_FCR_FCMD_GLB  = 0x0A # Get Lock Bit
EEFC_FCR_FCMD_SGPB = 0x0B # Set GPNVM Bit
EEFC_FCR_FCMD_CGPB = 0x0C # Clear GPNVM Bit
EEFC_FCR_FCMD_GGPB = 0x0D # Get GPNVM Bit
EEFC_FCR_FCMD_STUI = 0x0E # Start Read Unique Identifier
EEFC_FCR_FCMD_SPUI = 0x0F # Stop Read Unique Identifier

RSTC_CR = 0x400E1400
RSTC_MR = 0x400E1408

RSTC_CR_PROCRST = 0b0001
RSTC_CR_EXTRST  = 0b0100

RSTC_MR_URSTEN  = 0b0001
RSTC_MR_URSTIEN = 0b1000

RSTC_CR_FEY = 0xA5
RSTC_MR_FEY = 0xA5

# http://www.varsanofiev.com/inside/at91_sam_ba.htm
# http://sourceforge.net/apps/mediawiki/lejos/index.php?title=Documentation:SAM-BA

class SAMBAException(Exception):
    pass

class SAMBA:
    def __init__(self, port_name):
        try:
            self.port = Serial(port_name, 115200, timeout=5)
        except SerialException, e:
            if '[Errno 13]' in str(e):
                raise SAMBAException("No permission to open serial port")
            else:
                raise e

        self.port.write('N#')

        if self.port.read(2) != '\n\r':
            raise SAMBAException('No Brick in Bootloader found')

        chipid = self.read_uint32(CHIPID_CIDR)
        arch = (chipid >> 20) & 0b11111111

        if arch == ATSAM3SxB:
            self.flash_base = 0x400000
            self.flash_size = 0x20000
            self.flash_page_count = 512
            self.flash_page_size = 256
            self.flash_lockbit_count = 8
        elif arch == ATSAM3SxC:
            self.flash_base = 0x400000
            self.flash_size = 0x40000
            self.flash_page_count = 1024
            self.flash_page_size = 256
            self.flash_lockbit_count = 16
        else:
            raise SAMBAException('Brick with unknown SAM3S architecture: 0x%X' % arch)

        self.flash_lockregion_size = self.flash_size / self.flash_lockbit_count
        self.flash_pages_per_lockregion = self.flash_lockregion_size / self.flash_page_size

    def read_uid(self):
        self.write_flash_command(EEFC_FCR_FCMD_STUI, 0)
        self.wait_for_flash_ready(False)

        uid1 = self.read_uint32(self.flash_base + 8)
        uid2 = self.read_uint32(self.flash_base + 12)

        self.write_flash_command(EEFC_FCR_FCMD_SPUI, 0)
        self.wait_for_flash_ready()

        return uid2 << 32 | uid1

    def flash(self, firmware, imu_calibration, lock_imu_calibration_pages, progress):
        # Split firmware into pages
        firmware_pages = []
        offset = 0

        while offset < len(firmware):
            page = firmware[offset:offset + self.flash_page_size]

            if len(page) < self.flash_page_size:
                page += '\xff' * (self.flash_page_size - len(page))

            firmware_pages.append(page)
            offset += self.flash_page_size

        # Flash Programming Erata: FWS must be 6
        self.write_uint32(EEFC_FMR, 0x06 << 8)

        # Unlock
        for region in range(self.flash_lockbit_count):
            self.wait_for_flash_ready()
            page_num = (region * self.flash_page_count) / self.flash_lockbit_count
            self.write_flash_command(EEFC_FCR_FCMD_CLB, page_num)

        # Erase All
        self.wait_for_flash_ready()
        self.write_flash_command(EEFC_FCR_FCMD_EA, 0)
        self.wait_for_flash_ready()

        # Write firmware
        self.write_pages(firmware_pages, 0, 'Writing firmware', progress)

        # Write IMU calibration
        if imu_calibration is not None:
            progress.setLabelText('Writing IMU calibration')
            progress.setMaximum(0)
            progress.setValue(0)
            progress.show()

            ic_relative_address = self.flash_size - 0x1000 * 2 - 12 - 0x400
            ic_prefix_length = ic_relative_address % self.flash_page_size
            ic_prefix_address = self.flash_base + ic_relative_address - ic_prefix_length
            ic_prefix = ''
            offset = 0

            while len(ic_prefix) < ic_prefix_length:
                address = ic_prefix_address + offset
                ic_prefix += self.read_word(ic_prefix_address + offset)
                offset += 4

            prefixed_imu_calibration = ic_prefix + imu_calibration

            # Split IMU calibration into pages
            imu_calibration_pages = []
            offset = 0

            while offset < len(prefixed_imu_calibration):
                page = prefixed_imu_calibration[offset:offset + self.flash_page_size]

                if len(page) < self.flash_page_size:
                    page += '\xff' * (self.flash_page_size - len(page))

                imu_calibration_pages.append(page)
                offset += self.flash_page_size

            # Write IMU calibration
            page_num_offset = (ic_relative_address - ic_prefix_length) / self.flash_page_size

            self.write_pages(imu_calibration_pages, page_num_offset, 'Writing IMU calibration', progress)

        # Lock firmware
        self.lock_pages(0, len(firmware_pages))

        # Lock IMU calibration
        if imu_calibration is not None and lock_imu_calibration_pages:
            first_page_num = (ic_relative_address - ic_prefix_length) / self.flash_page_size
            self.lock_pages(first_page_num, len(imu_calibration_pages))

        # Set Boot-from-Flash bit
        self.wait_for_flash_ready()
        self.write_flash_command(EEFC_FCR_FCMD_SGPB, 1)
        self.wait_for_flash_ready()

        # Verify firmware
        self.verify_pages(firmware_pages, 0, 'firmware', imu_calibration is not None, progress)

        # Verify IMU calibration
        if imu_calibration is not None:
            page_num_offset = (ic_relative_address - ic_prefix_length) / self.flash_page_size
            self.verify_pages(imu_calibration_pages, page_num_offset, 'IMU calibration', True, progress)

        # Boot
        self.reset()

    def write_pages(self, pages, page_num_offset, title, progress):
        progress.setLabelText(title)
        progress.setMaximum(len(pages))
        progress.setValue(0)
        progress.show()

        page_num = 0

        for page in pages:
            offset = 0

            while offset < len(page):
                address = self.flash_base + (page_num_offset + page_num) * self.flash_page_size + offset
                self.write_word(address, page[offset:offset + 4])
                offset += 4

            self.wait_for_flash_ready()
            self.write_flash_command(EEFC_FCR_FCMD_WP, page_num_offset + page_num)
            self.wait_for_flash_ready()

            page_num += 1
            progress.setValue(page_num)
            QApplication.processEvents()

    def verify_pages(self, pages, page_num_offset, title, title_in_error, progress):
        progress.setLabelText('Verifying written ' + title)
        progress.setMaximum(len(pages))
        progress.setValue(0)
        progress.show()

        offset = page_num_offset * self.flash_page_size
        page_num = 0

        for page in pages:
            read_page = self.read_bytes(self.flash_base + offset, len(page))
            offset += len(page)

            if read_page != page:
                if title_in_error:
                    raise SAMBAException('Verification error ({0})'.format(title))
                else:
                    raise SAMBAException('Verification error')

            page_num += 1
            progress.setValue(page_num)
            QApplication.processEvents()

    def lock_pages(self, page_num, page_count):
        start_page_num = page_num - (page_num % self.flash_pages_per_lockregion)
        end_page_num = page_num + page_count

        if (end_page_num % self.flash_pages_per_lockregion) != 0:
            end_page_num += self.flash_pages_per_lockregion - (end_page_num % self.flash_pages_per_lockregion)

        for region in range(start_page_num / self.flash_pages_per_lockregion,
                            end_page_num / self.flash_pages_per_lockregion):
            self.wait_for_flash_ready()
            page_num = (region * self.flash_page_count) / self.flash_lockbit_count
            self.write_flash_command(EEFC_FCR_FCMD_SLB, page_num)

        self.wait_for_flash_ready()

    def read_word(self, address): # 4 bytes
        try:
            self.port.write('w%08X,4#' % address)
            return self.port.read(4)
        except:
            raise SAMBAException('Read error')

    def write_word(self, address, value): # 4 bytes
        self.write_uint32(address, struct.unpack('<I', value)[0])

    def read_uint32(self, address):
        return struct.unpack('<I', self.read_word(address))[0]

    def write_uint32(self, address, value):
        try:
            self.port.write('W%08X,%08X#' % (address, value))
        except:
            raise SAMBAException('Write error')

    def write_uint32(self, address, value):
        try:
            self.port.write('W%08X,%08X#' % (address, value))
        except:
            raise SAMBAException('Write error')

    def read_bytes(self, address, length):
        try:
            self.port.write('R%0X,%0X#' % (address, length))
            return self.port.read(length)
        except:
            raise SAMBAException('Read error')

    def reset(self):
        try:
            self.write_uint32(RSTC_MR, (RSTC_MR_FEY << 24) | (10 << 8) | RSTC_MR_URSTEN | RSTC_MR_URSTIEN)
            self.write_uint32(RSTC_CR, (RSTC_CR_FEY << 24) | RSTC_CR_PROCRST | RSTC_CR_EXTRST)
        except:
            raise SAMBAException('Reset error')

    def go(self, address):
        try:
            self.port.write('G%08X#' % address)
        except:
            raise SAMBAException('Execution error')

    def wait_for_flash_ready(self, ready=True):
        for i in range(1000):
            fsr = self.read_uint32(EEFC_FSR)

            if (fsr & EEFC_FSR_FLOCKE) != 0:
                raise SAMBAException('Flash locking error')

            if (fsr & EEFC_FSR_FCMDE) != 0:
                raise SAMBAException('Flash command error')

            if ready:
                if (fsr & EEFC_FSR_FRDY) != 0:
                    break
            else:
                if (fsr & EEFC_FSR_FRDY) == 0:
                    break
        else:
            raise SAMBAException('Flash timeout')

    def write_flash_command(self, command, argument):
        self.write_uint32(EEFC_FCR, (EEFC_FCR_FKEY << 24) | (argument << 8) | command)

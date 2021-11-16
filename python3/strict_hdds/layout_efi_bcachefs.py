#!/usr/bin/env python3

# Copyright (c) 2020-2021 Fpemud <fpemud@sina.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.


import os
from .util import Util, GptUtil, BcachefsUtil, EfiCacheGroup
from .handy import MountEfi, CommonChecks, HandyUtil
from . import errors
from . import StorageLayout


class StorageLayoutImpl(StorageLayout):
    """Layout:
           /dev/sda                         SSD, GPT (cache-disk)
               /dev/sda1                    ESP partition
               /dev/sda2                    swap device
               /dev/sda3                    bcachefs cache device
           /dev/sdb                         Non-SSD, GPT
               /dev/sdb1                    reserved ESP partition
               /dev/sdb2                    bcachefs backing device
           /dev/sdc                         Non-SSD, GPT
               /dev/sdc1                    reserved ESP partition
               /dev/sdc2                    bcachefs backing device
           /dev/sda3:/dev/sdb2:/dev/sdc2    root device
       Description:
           1. /dev/sda1 and /dev/sd{b,c}1 must has the same size
           2. /dev/sda1, /dev/sda2 and /dev/sda3 is order-sensitive, no extra partition is allowed
           3. /dev/sd{b,c}1 and /dev/sd{b,c}2 is order-sensitive, no extra partition is allowed
           4. cache-disk is optional, and only one cache-disk is allowed at most
           5. cache-disk can have no swap partition, /dev/sda2 would be the cache device then
           6. extra harddisk is allowed to exist
    """

    def __init__(self, mount_dir):
        self._cg = None                     # EfiCacheGroup
        self._mnt = None                    # MountEfi

    @property
    def boot_mode(self):
        return StorageLayout.BOOT_MODE_EFI

    @property
    def dev_rootfs(self):
        return ":".join(self.get_disk_list())

    @property
    @EfiCacheGroup.proxy
    def dev_boot(self):
        pass

    @property
    @EfiCacheGroup.proxy
    def dev_swap(self):
        pass

    @property
    @EfiCacheGroup.proxy
    def boot_disk(self):
        pass

    def umount_and_dispose(self):
        if True:
            self._mnt.umount()
            del self._mnt
        if True:
            # FIXME: stop and unregister bcache
            del self._cg

    @MountEfi.proxy
    def remount_rootfs(self, mount_options):
        pass

    @MountEfi.proxy
    def get_bootdir_rw_controller(self):
        pass

    def check(self):
        CommonChecks.storageLayoutCheckSwapSize(self)

    @EfiCacheGroup.proxy
    def get_esp(self):
        pass

    @EfiCacheGroup.proxy
    def get_pending_esp_list(self):
        pass

    @EfiCacheGroup.proxy
    def sync_esp(self, dst):
        pass

    @EfiCacheGroup.proxy
    def get_disk_list(self):
        pass

    @EfiCacheGroup.proxy
    def get_ssd(self):
        pass

    @EfiCacheGroup.proxy
    def get_ssd_esp_partition(self):
        pass

    @EfiCacheGroup.proxy
    def get_ssd_swap_partition(self):
        pass

    @EfiCacheGroup.proxy
    def get_ssd_cache_partition(self):
        pass

    @EfiCacheGroup.proxy
    def get_hdd_list(self):
        pass

    @EfiCacheGroup.proxy
    def get_hdd_esp_partition(self, disk):
        pass

    @EfiCacheGroup.proxy
    def get_hdd_data_partition(self, disk):
        pass

    def add_disk(self, disk):
        assert disk is not None

        if disk not in Util.getDevPathListForFixedDisk():
            raise errors.StorageLayoutAddDiskError(disk, errors.NOT_DISK)

        if Util.isBlkDevSsdOrHdd(disk):
            self._cg.add_ssd(disk)

            # ssd partition 3: make it as cache device and add it to bcachefs
            parti = self._cg.get_ssd_cache_partition()
            BcachefsUtil.makeDevice(parti)

            return True     # boot disk changed
        else:
            lastBootHdd = self._cg.boot_disk

            self._cg.add_hdd(disk)

            # hdd partition 2: make it as backing device and add it to bcachefs
            parti = self._cg.get_hdd_data_partition(disk)
            BcachefsUtil.makeDevice(parti)
            Util.cmdCall("/sbin/bcachefs", "device", "add", parti, "/")

            return lastBootHdd != self._cg.boot_disk     # boot disk may change

    def remove_disk(self, devpath):
        assert devpath is not None

        if self._cg.get_ssd() is not None and devpath == self._cg.get_ssd():
            if self._cg.get_ssd_swap_partition() is not None:
                if Util.systemdFindSwapService(self._cg.get_ssd_swap_partition()) is not None:
                    raise errors.StorageLayoutRemoveDiskError(errors.SWAP_IS_IN_USE)

            # ssd partition 3: remove from bcachefs
            BcachefsUtil.removeDevice(self._cg.get_ssd_cache_partition())

            # remove
            self._cg.remove_ssd()

            return True     # boot disk changed
        else:
            assert devpath in self._cg.get_hdd_list()

            if len(self._cg.get_hdd_list()) <= 1:
                raise errors.StorageLayoutRemoveDiskError(errors.CAN_NOT_REMOVE_LAST_HDD)

            lastBootHdd = self._cg.boot_disk

            # hdd partition 2: remove from bcachefs
            BcachefsUtil.removeDevice(self._cg.get_hdd_data_partition(devpath))

            # remove
            self._cg.remove_hdd(devpath)

            return lastBootHdd != self._cg.boot_disk     # boot disk may change


def parse(boot_dev, root_dev):
    if not GptUtil.isEspPartition(boot_dev):
        raise errors.StorageLayoutParseError(StorageLayoutImpl.name, errors.BOOT_DEV_IS_NOT_ESP)

    # get ssd, hdd list
    ssd, hddList = HandyUtil.getSsdAndHddList(BcachefsUtil.getSlaveSsdDevPatListAndHddDevPathList(root_dev))

    # ssd
    if ssd is not None:
        if ssd != Util.devPathPartiToDisk(boot_dev):
            raise XXXXX
    ssdEspParti, ssdSwapParti, ssdCacheParti = HandyUtil.cacheGroupGetSsdPartitions(StorageLayoutImpl.name, root_dev, ssd)

    # boot harddisk
    if ssd is not None:
        bootHdd = None
    else:
        bootHdd = Util.devPathPartiToDisk(boot_dev)

    ret = StorageLayoutImpl()
    ret._cg = EfiCacheGroup(ssd=ssd, ssdEspParti=ssdEspParti, ssdSwapParti=ssdSwapParti, ssdCacheParti=ssdCacheParti, hddList=hddList, bootHdd=bootHdd)
    ret._mnt = MountEfi("/")
    return ret


def create_and_mount(ssd=None, hdd_list=None):
    if ssd is None and hdd_list is None:
        ssd_list, hdd_list = Util.getDevPathListForFixedSsdAndHdd()
        if len(ssd_list) == 0:
            pass
        elif len(ssd_list) == 1:
            ssd = ssd_list[0]
        else:
            raise errors.StorageLayoutCreateError(errors.MULTIPLE_SSD)
        if len(hdd_list) == 0:
            raise errors.StorageLayoutCreateError(errors.NO_DISK_WHEN_CREATE)
    else:
        assert hdd_list is not None and len(hdd_list) > 0

    ret = StorageLayoutImpl()

    ret._cg = EfiCacheGroup()

    # add disks, process ssd first so that minimal boot disk change is need
    if ssd is not None:
        ret._cg.add_ssd(ssd)
    for hdd in hdd_list:
        ret._cg.add_hdd(hdd)

    # create bcachefs
    if ret._cg.get_ssd() is not None:
        ssd_list2 = [ret._cg.get_ssd_cache_partition()]
    else:
        ssd_list2 = []
    hdd_list2 = [ret._cg.get_hdd_data_partition(x) for x in hdd_list]
    BcachefsUtil.createBcachefs(ssd_list2, hdd_list2, 1, 1)

    return ret
